"""Versioned catalog and server-owned capability boundary for advisory agents."""

import asyncio
import json
from hashlib import sha256
from dataclasses import dataclass
from threading import Lock
from typing import Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_catalog import AGENT_CATALOG
from ai_quant_os.agent_permissions import KNOWN_TOOLS, READ_TOOLS, ROLE_CEILINGS, SANDBOX_TOOLS

Lifecycle = Literal["ACTIVE", "LIMITED", "SHADOW", "QUARANTINED"]


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    name: str = Field(min_length=1, max_length=128)
    squad: Literal[
        "Control",
        "Market",
        "Data",
        "Research",
        "Validation",
        "Portfolio",
        "Learning",
        "Reliability",
        "Evolution",
        "Safety",
    ]
    mission: str = Field(min_length=1, max_length=1000)
    output_type: str = Field(min_length=1, max_length=128)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=32)
    allowed_tools: tuple[str, ...] = Field(max_length=32)
    implementation_status: Literal["PLANNED", "PARTIAL", "IMPLEMENTED"] = "PLANNED"
    timeout_seconds: float = Field(gt=0, le=30, allow_inf_nan=False, default=2)

    @model_validator(mode="after")
    def bounded_role(self) -> "AgentManifest":
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or not set(
            self.allowed_tools
        ).issubset(ROLE_CEILINGS[self.agent_id]):
            raise ValueError("Manifest tools exceed fixed role ceiling")
        if len(set(self.required_inputs)) != len(self.required_inputs) or any(
            not value or len(value) > 128 for value in self.required_inputs
        ):
            raise ValueError("Invalid manifest input requirements")
        return self

    @property
    def manifest_ref(self) -> str:
        return "agent-manifest:" + sha256(self.model_dump_json().encode()).hexdigest()


class AgentRegistry:
    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str], AgentManifest] = {}
        self._states: dict[tuple[str, str], Lifecycle] = {}
        self._quarantine_pins: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    def register(self, manifest: AgentManifest) -> bool:
        manifest = AgentManifest.model_validate_json(manifest.model_dump_json())
        key = (manifest.agent_id, manifest.agent_version)
        with self._lock:
            prior = self._manifests.get(key)
            if prior:
                if prior != manifest:
                    raise ValueError("Immutable agent version conflict")
                return False
            if len(self._manifests) >= 1024:
                raise ValueError("Agent registry capacity exhausted")
            self._manifests[key] = manifest
            self._states[key] = "SHADOW"
            return True

    def get(self, agent_id: str, version: str) -> AgentManifest:
        with self._lock:
            manifest = self._manifests.get((agent_id, version))
        if manifest is None:
            raise ValueError("Unknown agent version")
        return manifest

    def manifests(self) -> tuple[AgentManifest, ...]:
        with self._lock:
            return tuple(self._manifests[key] for key in sorted(self._manifests))

    def lifecycle(self, agent_id: str, version: str) -> Lifecycle:
        with self._lock:
            key = (agent_id, version)
            if key not in self._states:
                raise ValueError("Unknown agent version")
            return self._states[key]

    def set_lifecycle(self, agent_id: str, version: str, state: Lifecycle) -> None:
        if state not in {"ACTIVE", "LIMITED", "SHADOW", "QUARANTINED"}:
            raise ValueError("Unknown agent lifecycle")
        with self._lock:
            key = (agent_id, version)
            if key not in self._manifests:
                raise ValueError("Unknown agent version")
            if key in self._quarantine_pins and state != "QUARANTINED":
                raise PermissionError("Pinned quarantine requires independently gated recovery")
            self._states[key] = state

    def quarantine(self, agent_id: str, version: str, manifest_ref: str) -> None:
        with self._lock:
            key = (agent_id, version)
            manifest = self._manifests.get(key)
            if manifest is None or manifest.manifest_ref != manifest_ref:
                raise ValueError("Quarantine manifest pin mismatch")
            self._quarantine_pins[key] = manifest_ref
            self._states[key] = "QUARANTINED"

    def require_tool(self, agent_id: str, version: str, tool: str, *, scope: str) -> None:
        with self._lock:
            key = (agent_id, version)
            manifest = self._manifests.get(key)
            state = self._states.get(key)
            if (
                manifest is None
                or scope not in {"PAPER", "SANDBOX"}
                or tool not in manifest.allowed_tools
                or tool not in KNOWN_TOOLS
                or state == "QUARANTINED"
                or (state == "LIMITED" and tool not in READ_TOOLS)
                or (tool in SANDBOX_TOOLS and scope != "SANDBOX")
            ):
                raise PermissionError("Agent tool access denied")


@dataclass(frozen=True)
class ToolInvocation:
    agent_id: str
    agent_version: str
    tool: str
    scope: str
    payload: dict[str, object]
    context_ref: str | None = None
    task_id: str | None = None


class CallBudget(Protocol):
    def reserve(self, invocation: ToolInvocation) -> None: ...


class AgentToolBoundary:
    def __init__(
        self,
        registry: AgentRegistry,
        handlers: dict[str, Callable[[ToolInvocation], Awaitable[dict[str, object]]]],
        *,
        call_budget: CallBudget | None = None,
    ) -> None:
        if not set(handlers).issubset(KNOWN_TOOLS) or any(
            not callable(handler) for handler in handlers.values()
        ):
            raise ValueError("Unrecognized agent tool handler")
        self._registry = registry
        self._handlers = handlers.copy()
        self._call_budget = call_budget

    def capabilities(self, agent_id: str, version: str) -> tuple[str, ...]:
        manifest = self._registry.get(agent_id, version)
        tools = []
        for tool in manifest.allowed_tools:
            scope = "SANDBOX" if tool in SANDBOX_TOOLS else "PAPER"
            try:
                self._registry.require_tool(agent_id, version, tool, scope=scope)
            except PermissionError:
                continue
            if tool in self._handlers:
                tools.append(tool)
        return tuple(sorted(tools))

    async def call(
        self,
        agent_id: str,
        version: str,
        tool: str,
        *,
        scope: str,
        payload: dict[str, object],
        context_ref: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, object]:
        self._registry.require_tool(agent_id, version, tool, scope=scope)
        handler = self._handlers.get(tool)
        if handler is None:
            raise ValueError("Agent tool unavailable")
        if not isinstance(payload, dict):
            raise ValueError("Agent tool input must be an object")
        if {"agent_id", "agent_version", "tool", "scope"}.intersection(payload):
            raise ValueError("Agent tool input contains authority fields")
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        if len(raw.encode()) > 16384:
            raise ValueError("Agent tool input exceeds limit")
        invocation = ToolInvocation(
            agent_id, version, tool, scope, json.loads(raw), context_ref, task_id
        )
        if self._call_budget is not None:
            await asyncio.to_thread(self._call_budget.reserve, invocation)
            self._registry.require_tool(agent_id, version, tool, scope=scope)
        timeout = self._registry.get(agent_id, version).timeout_seconds
        try:
            result = await asyncio.wait_for(handler(invocation), timeout=timeout)
        except TimeoutError:
            raise TimeoutError("Agent tool timed out") from None
        except Exception:
            raise ValueError("Agent tool failed") from None
        self._registry.require_tool(agent_id, version, tool, scope=scope)
        if (
            not isinstance(result, dict)
            or len(json.dumps(result, allow_nan=False).encode()) > 65536
        ):
            raise ValueError("Agent tool output exceeds contract")
        return result


def load_agent_catalog() -> AgentRegistry:
    registry = AgentRegistry()
    for identifier, name, squad, mission, output, inputs in AGENT_CATALOG:
        registry.register(
            AgentManifest(
                agent_id=identifier,
                agent_version="0.1.0",
                name=name,
                squad=squad,
                mission=mission,
                output_type=output,
                required_inputs=inputs,
                allowed_tools=tuple(sorted(ROLE_CEILINGS[identifier])),
            )
        )
    for identifier in (
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A15",
        "A18",
    ):
        original = registry.get(identifier, "0.1.0")
        registry.register(
            AgentManifest.model_validate(
                {
                    **original.model_dump(),
                    "agent_version": "0.2.0",
                    "implementation_status": "PARTIAL",
                    **({"timeout_seconds": 5} if identifier in {"A01", "A11"} else {}),
                }
            )
        )
    registry.register(
        AgentManifest.model_validate(
            {**registry.get("A01", "0.2.0").model_dump(), "agent_version": "0.3.0"}
        )
    )
    registry.register(
        AgentManifest.model_validate(
            {
                **registry.get("A01", "0.3.0").model_dump(),
                "agent_version": "0.4.0",
                "required_inputs": (*registry.get("A01", "0.3.0").required_inputs, "task_spec"),
            }
        )
    )
    return registry
