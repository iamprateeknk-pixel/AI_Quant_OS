"""Own MCP / tool fabric for the agent operating system (V3.2 section 11).

Agents receive only the tools their role requires, through an explicit
allow-list held by the caller. Tools are typed specs plus locally registered
handlers; this module is transport-thin and exposes an MCP-style JSON-RPC 2.0
surface for ``tools/list`` and ``tools/call``.

Hard guardrails (section 11 and the section 1 non-negotiable):

- No tool in the catalog submits, modifies or cancels a broker order, and no
  tool exposes broker credentials or secrets. Catalog construction rejects
  any spec whose name claims such a capability.
- Trading credentials are never agent tools. Broker access stays with the
  deterministic execution gateway, which this module does not import.
- Write-scope code and shadow/paper tools are ``sandbox_only``: they may act
  only inside isolated sandboxes or non-capital environments.
- Tools that need an operator decision carry ``requires_approval`` and refuse
  to run unless the call is explicitly approved (see ``ToolRegistry``).

Stdlib only. Handlers are plain callables taking one ``dict`` of validated
arguments and returning any JSON-friendly value.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

ToolHandler = Callable[[dict], object]

SCOPES = ("read", "simulate", "write")

#: Name fragments that must never appear in a tool name. Any of these would
#: mean an agent can place live orders or reach broker credentials, which
#: the blueprint forbids outright.
FORBIDDEN_TOOL_FRAGMENTS = (
    "place_order",
    "submit_order",
    "modify_order",
    "cancel_order",
    "broker_order",
    "live_order",
    "order_placement",
    "broker_credential",
    "broker_secret",
    "broker_password",
    "broker_api_key",
)


@dataclass(frozen=True)
class ToolSpec:
    """One typed tool an agent may be allow-listed for."""

    name: str
    family: str
    description: str
    input_schema: dict = field(default_factory=dict)
    scope: str = "read"
    requires_approval: bool = False
    sandbox_only: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("Tool name must be a non-empty identifier")
        lowered = self.name.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_TOOL_FRAGMENTS):
            raise ValueError(f"Tool {self.name!r} would expose broker order or credential access")
        if not self.family:
            raise ValueError("Tool family is required")
        if self.scope not in SCOPES:
            raise ValueError(f"Tool scope must be one of {SCOPES}")
        if not isinstance(self.input_schema, dict):
            raise TypeError("Tool input_schema must be a dict")

    @property
    def required_keys(self) -> tuple[str, ...]:
        """Argument names the caller must supply, per ``input_schema``."""
        required = self.input_schema.get("required", ())
        if not isinstance(required, (list, tuple)):
            raise TypeError(f"Tool {self.name!r} input_schema 'required' must be a list")
        return tuple(str(key) for key in required)

    def to_dict(self) -> dict:
        """MCP-style serialization used by ``tools/list``."""
        return {
            "name": self.name,
            "family": self.family,
            "description": self.description,
            "inputSchema": self.input_schema,
            "input_schema": self.input_schema,
            "scope": self.scope,
            "requires_approval": self.requires_approval,
            "sandbox_only": self.sandbox_only,
        }


def _schema(*required: str, **properties: str) -> dict:
    """Build a small JSON-schema object for a tool's arguments."""
    props = {name: {"type": kind} for name, kind in properties.items()}
    for name in required:
        props.setdefault(name, {"type": "string"})
    return {"type": "object", "properties": props, "required": list(required)}


def _spec(
    name: str,
    family: str,
    description: str,
    input_schema: dict,
    scope: str = "read",
    *,
    requires_approval: bool = False,
    sandbox_only: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        family=family,
        description=description,
        input_schema=input_schema,
        scope=scope,
        requires_approval=requires_approval,
        sandbox_only=sandbox_only,
    )


#: The nine tool families of section 11, in blueprint order.
TOOL_FAMILIES = (
    "Research",
    "Backtest",
    "Validation",
    "Code",
    "Shadow/Paper",
    "Portfolio",
    "Risk",
    "Operations",
    "Governance",
)

TOOL_CATALOG: tuple[ToolSpec, ...] = (
    # Research -- research environment only.
    _spec(
        "create_hypothesis",
        "Research",
        "Create a falsifiable research hypothesis in the research environment.",
        _schema("title", "mechanism", title="string", mechanism="string"),
        "write",
        sandbox_only=True,
    ),
    _spec(
        "query_dataset",
        "Research",
        "Query a versioned dataset available to research.",
        _schema("dataset_id", dataset_id="string"),
    ),
    _spec(
        "create_experiment",
        "Research",
        "Create an immutable experiment bound to a dataset and strategy version.",
        _schema("hypothesis_id", "dataset_version"),
        "write",
        sandbox_only=True,
    ),
    # Backtest -- immutable dataset/version required.
    _spec(
        "run_backtest",
        "Backtest",
        "Run a deterministic backtest in the sandbox.",
        _schema("strategy_version_id", "dataset_version"),
        "simulate",
        sandbox_only=True,
    ),
    _spec(
        "compare_runs",
        "Backtest",
        "Compare two recorded backtest runs.",
        _schema("run_id_a", "run_id_b"),
    ),
    _spec(
        "run_walk_forward",
        "Backtest",
        "Run a walk-forward evaluation in the sandbox.",
        _schema("strategy_version_id", "dataset_version"),
        "simulate",
        sandbox_only=True,
    ),
    # Validation -- cannot modify the candidate under test.
    _spec(
        "run_monte_carlo",
        "Validation",
        "Run Monte Carlo perturbations against a frozen candidate.",
        _schema("candidate_id"),
        "simulate",
    ),
    _spec(
        "leakage_scan",
        "Validation",
        "Scan a frozen candidate for look-ahead and timestamp leakage.",
        _schema("candidate_id"),
        "simulate",
    ),
    _spec(
        "stress_regime",
        "Validation",
        "Stress a frozen candidate against adverse regimes.",
        _schema("candidate_id", "regime"),
        "simulate",
    ),
    _spec(
        "capacity_test",
        "Validation",
        "Test turnover, depth and impact capacity of a frozen candidate.",
        _schema("candidate_id"),
        "simulate",
    ),
    # Code -- isolated sandbox; no production network or secrets.
    _spec(
        "create_branch",
        "Code",
        "Create an isolated sandbox branch/worktree for a candidate.",
        _schema("candidate_id"),
        "write",
        sandbox_only=True,
    ),
    _spec(
        "run_tests",
        "Code",
        "Run tests for a candidate inside its sandbox.",
        _schema("candidate_id"),
        "simulate",
        sandbox_only=True,
    ),
    _spec(
        "build_strategy_artifact",
        "Code",
        "Build an immutable strategy artifact inside the sandbox.",
        _schema("candidate_id"),
        "write",
        sandbox_only=True,
    ),
    # Shadow/Paper -- non-capital environments only.
    _spec(
        "deploy_shadow",
        "Shadow/Paper",
        "Deploy a validated candidate to the shadow (non-capital) arena.",
        _schema("candidate_id"),
        "write",
        requires_approval=True,
        sandbox_only=True,
    ),
    _spec(
        "pause_shadow",
        "Shadow/Paper",
        "Pause a shadow deployment; reduces activity, never adds capital.",
        _schema("candidate_id"),
        "write",
        sandbox_only=True,
    ),
    _spec(
        "compare_challenger",
        "Shadow/Paper",
        "Compare a shadow challenger against the current champion.",
        _schema("challenger_id", "champion_id"),
    ),
    # Portfolio -- read/simulation by default.
    _spec(
        "get_exposure",
        "Portfolio",
        "Read current portfolio exposure.",
        _schema(),
    ),
    _spec(
        "simulate_allocation",
        "Portfolio",
        "Simulate an allocation without changing any state.",
        _schema("weights"),
        "simulate",
    ),
    _spec(
        "stress_portfolio",
        "Portfolio",
        "Run a stress scenario against the current portfolio snapshot.",
        _schema("scenario"),
        "simulate",
    ),
    # Risk -- agents cannot set global limits.
    _spec(
        "get_risk_state",
        "Risk",
        "Read the deterministic risk kernel state.",
        _schema(),
    ),
    _spec(
        "simulate_risk_change",
        "Risk",
        "Simulate a risk change; never applies it.",
        _schema("proposal"),
        "simulate",
    ),
    # Operations -- write actions require policy/approval elsewhere.
    _spec(
        "get_broker_health",
        "Operations",
        "Read broker route health observations (no credentials).",
        _schema("broker_id"),
    ),
    _spec(
        "get_incident",
        "Operations",
        "Read an incident record.",
        _schema("incident_id"),
    ),
    _spec(
        "replay_session",
        "Operations",
        "Replay a recorded session deterministically.",
        _schema("session_id"),
        "simulate",
    ),
    # Governance -- fully audited.
    _spec(
        "get_policy",
        "Governance",
        "Read a governance policy by id.",
        _schema("policy_id"),
    ),
    _spec(
        "create_approval_request",
        "Governance",
        "Create an audited approval request for a human decision.",
        _schema("action", "reason"),
        "write",
    ),
    _spec(
        "export_evidence",
        "Governance",
        "Export an evidence pack for audit.",
        _schema("evidence_id"),
    ),
)


def _validate_catalog(catalog: tuple[ToolSpec, ...]) -> None:
    names = [spec.name for spec in catalog]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate tool names in catalog")
    families = {spec.family for spec in catalog}
    missing = [family for family in TOOL_FAMILIES if family not in families]
    if missing:
        raise ValueError(f"Tool catalog is missing families: {missing}")
    for spec in catalog:
        is_sandbox_family_write = spec.scope == "write" and spec.family in (
            "Code",
            "Shadow/Paper",
        )
        if is_sandbox_family_write and not spec.sandbox_only:
            raise ValueError(f"Write tool {spec.name!r} in {spec.family} must be sandbox_only")


_validate_catalog(TOOL_CATALOG)

_CATALOG_BY_NAME = {spec.name: spec for spec in TOOL_CATALOG}


class ToolRegistry:
    """Allow-list enforcement and dispatch for catalog tools.

    The registry holds no authority of its own: a call succeeds only when
    the tool is in the caller's ``agent_tools`` allow-list, a handler has
    been registered for it, the required arguments are present, and -- for
    ``requires_approval`` tools -- the call carries ``"_approved": True``.
    The approval marker is stripped before the handler sees the arguments.
    """

    def __init__(
        self,
        catalog: tuple[ToolSpec, ...] = TOOL_CATALOG,
        handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        _validate_catalog(catalog)
        self._specs = {spec.name: spec for spec in catalog}
        self._order = tuple(spec.name for spec in catalog)
        self._handlers: dict[str, ToolHandler] = dict(handlers or {})

    def register_handler(self, name: str, handler: ToolHandler) -> None:
        """Register the callable that implements a catalog tool."""
        if name not in self._specs:
            raise ValueError(f"Unknown tool: {name}")
        if not callable(handler):
            raise TypeError("Tool handler must be callable")
        self._handlers[name] = handler

    # Short alias: registering a handler is the only kind of registration.
    register = register_handler

    def get_spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ValueError(f"Unknown tool: {name}") from None

    def list_tools(self, agent_tools: set[str]) -> tuple[ToolSpec, ...]:
        """Return catalog specs for exactly the caller's allow-list."""
        allowed = set(agent_tools)
        return tuple(self._specs[name] for name in self._order if name in allowed)

    def call_tool(
        self,
        agent_tools: set[str],
        name: str,
        arguments: dict | None = None,
    ) -> object:
        """Validate and dispatch one tool call.

        Raises ``PermissionError`` for an unknown tool, a tool outside the
        allow-list, or a missing approval; ``ValueError`` for missing
        required arguments; ``LookupError`` when no handler is registered.
        """
        spec = self._specs.get(name)
        if spec is None:
            raise PermissionError(f"Unknown tool: {name}")
        if name not in set(agent_tools):
            raise PermissionError(f"Tool {name!r} is not in the agent allow-list")
        args = dict(arguments or {})
        if spec.requires_approval and args.get("_approved") is not True:
            raise PermissionError(f"Tool {name!r} requires approval")
        args.pop("_approved", None)
        missing = [key for key in spec.required_keys if key not in args]
        if missing:
            raise ValueError(f"Tool {name!r} is missing required arguments: {missing}")
        handler = self._handlers.get(name)
        if handler is None:
            raise LookupError(f"No handler registered for tool: {name}")
        return handler(args)

    # -- MCP-style JSON-RPC 2.0 surface ------------------------------------

    def handle_jsonrpc(self, request: dict) -> dict:
        """Handle one MCP-style JSON-RPC 2.0 request for tools/list|call."""
        if not isinstance(request, dict):
            return _rpc_error(None, -32600, "Invalid request")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "Params must be an object")
        if method == "tools/list":
            agent_tools = params.get("agent_tools")
            specs = (
                self.list_tools(set(agent_tools))
                if agent_tools is not None
                else tuple(self._specs[name] for name in self._order)
            )
            result = {"tools": [spec.to_dict() for spec in specs]}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            agent_tools = params.get("agent_tools")
            if not isinstance(name, str) or not name:
                return _rpc_error(request_id, -32602, "tools/call requires a tool name")
            if not isinstance(arguments, dict):
                return _rpc_error(request_id, -32602, "Tool arguments must be an object")
            # Without an explicit allow-list only read tools may be called;
            # an allow-list supplied in params is honoured exactly.
            allowed = (
                set(agent_tools)
                if agent_tools is not None
                else {spec.name for spec in TOOL_CATALOG if spec.scope == "read"}
            )
            try:
                value = self.call_tool(allowed, name, arguments)
            except PermissionError as error:
                return _rpc_error(request_id, -32001, str(error))
            except LookupError as error:
                return _rpc_error(request_id, -32601, str(error))
            except ValueError as error:
                return _rpc_error(request_id, -32602, str(error))
            result = {"tool": name, "result": value}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        return _rpc_error(request_id, -32601, f"Unknown method: {method}")


def _rpc_error(request_id: object, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


__all__ = [
    "FORBIDDEN_TOOL_FRAGMENTS",
    "SCOPES",
    "TOOL_CATALOG",
    "TOOL_FAMILIES",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
]
