"""V3.2 section 11: own MCP / tool fabric guardrails and dispatch."""

import pytest

from ai_quant_os.mcp_tools import (
    TOOL_CATALOG,
    TOOL_FAMILIES,
    ToolRegistry,
    ToolSpec,
)

EXPECTED_TOOLS = {
    "Research": {"create_hypothesis", "query_dataset", "create_experiment"},
    "Backtest": {"run_backtest", "compare_runs", "run_walk_forward"},
    "Validation": {"run_monte_carlo", "leakage_scan", "stress_regime", "capacity_test"},
    "Code": {"create_branch", "run_tests", "build_strategy_artifact"},
    "Shadow/Paper": {"deploy_shadow", "pause_shadow", "compare_challenger"},
    "Portfolio": {"get_exposure", "simulate_allocation", "stress_portfolio"},
    "Risk": {"get_risk_state", "simulate_risk_change"},
    "Operations": {"get_broker_health", "get_incident", "replay_session"},
    "Governance": {"get_policy", "create_approval_request", "export_evidence"},
}


def _registry_with_echo() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in TOOL_CATALOG:
        registry.register_handler(spec.name, lambda args, _name=spec.name: (_name, args))
    return registry


def test_catalog_covers_all_nine_families_and_representative_tools():
    assert tuple(EXPECTED_TOOLS) == TOOL_FAMILIES
    by_family: dict[str, set[str]] = {}
    for spec in TOOL_CATALOG:
        by_family.setdefault(spec.family, set()).add(spec.name)
    assert by_family == EXPECTED_TOOLS
    assert len(TOOL_CATALOG) == 27
    assert len({spec.name for spec in TOOL_CATALOG}) == len(TOOL_CATALOG)
    for spec in TOOL_CATALOG:
        assert spec.scope in ("read", "simulate", "write")
        assert isinstance(spec.input_schema, dict)


def test_catalog_has_no_broker_order_or_credential_tool():
    forbidden = (
        "place_order",
        "submit_order",
        "cancel_order",
        "modify_order",
        "live_order",
        "credential",
        "secret",
        "password",
        "api_key",
    )
    for spec in TOOL_CATALOG:
        lowered = spec.name.lower()
        assert not any(fragment in lowered for fragment in forbidden), spec.name


def test_forbidden_tool_spec_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ToolSpec(name="place_order", family="Operations", description="bad")
    with pytest.raises(ValueError):
        ToolSpec(name="get_broker_credentials", family="Operations", description="bad")


def test_write_code_and_shadow_tools_are_sandbox_only():
    for spec in TOOL_CATALOG:
        if spec.scope == "write" and spec.family in ("Code", "Shadow/Paper"):
            assert spec.sandbox_only is True, spec.name


def test_list_tools_returns_only_allow_listed_specs():
    registry = _registry_with_echo()
    listed = registry.list_tools({"query_dataset", "run_backtest", "not_a_tool"})
    assert [spec.name for spec in listed] == ["query_dataset", "run_backtest"]
    assert registry.list_tools(set()) == ()


def test_call_tool_dispatches_with_validated_arguments():
    registry = _registry_with_echo()
    result = registry.call_tool(
        {"query_dataset"}, "query_dataset", {"dataset_id": "bars-v1", "extra": 1}
    )
    assert result == ("query_dataset", {"dataset_id": "bars-v1", "extra": 1})


def test_call_tool_rejects_unknown_and_not_allow_listed():
    registry = _registry_with_echo()
    with pytest.raises(PermissionError):
        registry.call_tool({"query_dataset"}, "does_not_exist", {})
    with pytest.raises(PermissionError):
        registry.call_tool({"query_dataset"}, "run_backtest", {"strategy_version_id": "s"})


def test_call_tool_rejects_missing_required_arguments():
    registry = _registry_with_echo()
    with pytest.raises(ValueError):
        registry.call_tool({"query_dataset"}, "query_dataset", {})


def test_call_tool_without_handler_is_a_lookup_error():
    registry = ToolRegistry()
    with pytest.raises(LookupError):
        registry.call_tool({"get_policy"}, "get_policy", {"policy_id": "p1"})


def test_approval_is_enforced_and_marker_is_stripped():
    seen: dict = {}
    registry = ToolRegistry()
    registry.register_handler("deploy_shadow", lambda args: seen.update(args) or "deployed")
    allowed = {"deploy_shadow"}
    args = {"candidate_id": "cand-1"}
    with pytest.raises(PermissionError):
        registry.call_tool(allowed, "deploy_shadow", dict(args))
    with pytest.raises(PermissionError):
        registry.call_tool(allowed, "deploy_shadow", {**args, "_approved": False})
    assert registry.call_tool(allowed, "deploy_shadow", {**args, "_approved": True}) == "deployed"
    assert seen == {"candidate_id": "cand-1"}


def test_jsonrpc_tools_list_is_allow_listed_and_mcp_shaped():
    registry = _registry_with_echo()
    response = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"agent_tools": ["get_risk_state", "get_policy"]},
        }
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["get_risk_state", "get_policy"]
    assert tools[0]["inputSchema"]["type"] == "object"
    assert tools[0]["scope"] == "read"


def test_jsonrpc_tools_list_without_allow_list_returns_full_catalog():
    registry = _registry_with_echo()
    response = registry.handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(response["result"]["tools"]) == len(TOOL_CATALOG)


def test_jsonrpc_tools_call_success():
    registry = _registry_with_echo()
    response = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "agent_tools": ["query_dataset"],
                "name": "query_dataset",
                "arguments": {"dataset_id": "bars-v1"},
            },
        }
    )
    assert response["result"]["tool"] == "query_dataset"
    assert response["result"]["result"] == ("query_dataset", {"dataset_id": "bars-v1"})


def test_jsonrpc_errors_for_unknown_method_tool_and_permission():
    registry = _registry_with_echo()
    unknown_method = registry.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}}
    )
    assert unknown_method["error"]["code"] == -32601

    unknown_tool = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"agent_tools": ["query_dataset"], "name": "nope", "arguments": {}},
        }
    )
    assert unknown_tool["error"]["code"] == -32001
    assert "error" in unknown_tool and "result" not in unknown_tool

    not_allowed = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "agent_tools": ["query_dataset"],
                "name": "run_backtest",
                "arguments": {"strategy_version_id": "s", "dataset_version": "d"},
            },
        }
    )
    assert not_allowed["error"]["code"] == -32001


def test_jsonrpc_tools_call_requires_approval_over_the_wire():
    registry = _registry_with_echo()
    response = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "agent_tools": ["deploy_shadow"],
                "name": "deploy_shadow",
                "arguments": {"candidate_id": "cand-1"},
            },
        }
    )
    assert response["error"]["code"] == -32001


def test_jsonrpc_tools_call_missing_arguments_is_invalid_params():
    registry = _registry_with_echo()
    response = registry.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "agent_tools": ["query_dataset"],
                "name": "query_dataset",
                "arguments": {},
            },
        }
    )
    assert response["error"]["code"] == -32602
