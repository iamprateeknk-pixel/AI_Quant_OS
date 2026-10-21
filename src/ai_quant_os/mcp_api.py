"""HTTP transport for the MCP tool fabric (V3.2 section 11).

The catalog contains no broker-order or credential tool -- that is
enforced in :mod:`ai_quant_os.mcp_tools` at spec construction and is not
weakened here. Allow-lists and approval gating are likewise the
registry's; this module only transports them over HTTP for known agents.
Calls that declare a token cost are admitted by the cost governor
(see :mod:`ai_quant_os.mcp_costs`) before the registry dispatches them.
"""

import re
from collections.abc import Callable, Collection, Mapping

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.mcp_costs import McpCostGovernor
from ai_quant_os.mcp_tools import TOOL_CATALOG, ToolRegistry

#: Names of every catalog tool whose scope is ``read``. This is the
#: default allow-list for known callers when no explicit mapping is given.
READ_TOOL_NAMES: frozenset[str] = frozenset(
    spec.name for spec in TOOL_CATALOG if spec.scope == "read"
)

_AGENT_ID_PATTERN = re.compile(r"A(0[1-9]|1[0-8])")


def _default_allow_list(agent_id: str) -> frozenset[str]:
    if agent_id == "operator" or _AGENT_ID_PATTERN.fullmatch(agent_id):
        return READ_TOOL_NAMES
    raise PermissionError(f"Unknown MCP agent: {agent_id}")


class McpToolService:
    """Allow-listed, cost-checked dispatch over a ``ToolRegistry``."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        agent_tools: Mapping[str, Collection[str]] | None = None,
        cost_governor: McpCostGovernor | None = None,
    ) -> None:
        self._registry = registry
        self._agent_tools = agent_tools
        self._cost_governor = cost_governor if cost_governor is not None else McpCostGovernor()

    def allow_list(self, agent_id: str) -> frozenset[str]:
        """Return the tool names ``agent_id`` may use, or refuse the agent."""
        if self._agent_tools is not None:
            try:
                return frozenset(self._agent_tools[agent_id])
            except KeyError:
                raise PermissionError(f"Unknown MCP agent: {agent_id}") from None
        return _default_allow_list(agent_id)

    def list_tools(self, agent_id: str) -> dict:
        allowed = self.allow_list(agent_id)
        return {
            "agent_id": agent_id,
            "tools": [spec.to_dict() for spec in self._registry.list_tools(set(allowed))],
        }

    def call_tool(
        self,
        agent_id: str,
        name: str,
        arguments: dict | None = None,
        *,
        approved: bool = False,
        estimated_tokens: int | None = None,
    ) -> dict:
        allowed = self.allow_list(agent_id)
        task_id = f"mcp:{agent_id}:{name}"
        if estimated_tokens is not None:
            self._cost_governor.check(
                agent_id=agent_id, task_id=task_id, estimated_tokens=estimated_tokens
            )
        args = dict(arguments or {})
        if approved is True:
            args["_approved"] = True
        value = self._registry.call_tool(set(allowed), name, args)
        if estimated_tokens:
            self._cost_governor.record(agent_id=agent_id, task_id=task_id, tokens=estimated_tokens)
        return {"tool": name, "result": value}


def paper_mcp_service(
    *,
    risk,
    agent_tools: Mapping[str, Collection[str]] | None = None,
    cost_governor: McpCostGovernor | None = None,
) -> McpToolService:
    """Build the paper-path MCP service.

    Only the ``get_risk_state`` read handler is registered; every other
    catalog tool fails closed with ``LookupError`` until its own gated
    wiring lands.
    """
    registry = ToolRegistry()

    def _get_risk_state(arguments: dict) -> dict:
        return {"safety_state": risk.safety_state.value, "mode": "PAPER"}

    registry.register_handler("get_risk_state", _get_risk_state)
    return McpToolService(registry, agent_tools=agent_tools, cost_governor=cost_governor)


class McpToolCallInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)
    approved: bool = False
    estimated_tokens: int | None = Field(default=None, ge=0)


def install_mcp_routes(app: FastAPI, require_operator: Callable, service: McpToolService) -> None:
    @app.get("/api/v1/mcp/tools", dependencies=[Depends(require_operator)])
    def list_tools(agent_id: str) -> dict:
        try:
            return service.list_tools(agent_id)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error

    @app.post("/api/v1/mcp/tools/call", dependencies=[Depends(require_operator)])
    def call_tool(payload: McpToolCallInput) -> dict:
        try:
            return service.call_tool(
                payload.agent_id,
                payload.name,
                payload.arguments,
                approved=payload.approved,
                estimated_tokens=payload.estimated_tokens,
            )
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except LookupError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


__all__ = [
    "READ_TOOL_NAMES",
    "McpToolCallInput",
    "McpToolService",
    "install_mcp_routes",
    "paper_mcp_service",
]
