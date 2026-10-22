"""V3.2 section 11: HTTP transport for the MCP tool fabric."""

from decimal import Decimal

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.cost_governor import BudgetHierarchy
from ai_quant_os.mcp_api import install_mcp_routes, paper_mcp_service
from ai_quant_os.mcp_costs import CostCapRefusal, McpCostGovernor
from ai_quant_os.mcp_tools import FORBIDDEN_TOOL_FRAGMENTS
from ai_quant_os.risk import RiskKernel, RiskLimits

HEADERS = {"X-Operator-Token": "op"}

AGENT_TOOLS = {
    "A01": {"get_risk_state", "deploy_shadow", "get_policy"},
    "A02": {"get_policy"},
}


def stub_require_operator(x_operator_token: str | None = Header(default=None)) -> None:
    if x_operator_token != "op":
        raise HTTPException(401, "Invalid operator token")


def _risk() -> RiskKernel:
    return RiskKernel(RiskLimits(Decimal(10), Decimal(20), 5))


def _client() -> TestClient:
    service = paper_mcp_service(risk=_risk(), agent_tools=AGENT_TOOLS)
    app = FastAPI()
    install_mcp_routes(app, stub_require_operator, service)
    return TestClient(app)


def _default_client() -> TestClient:
    service = paper_mcp_service(risk=_risk())
    app = FastAPI()
    install_mcp_routes(app, stub_require_operator, service)
    return TestClient(app)


def _call(client: TestClient, payload: dict):
    return client.post("/api/v1/mcp/tools/call", json=payload, headers=HEADERS)


def test_list_tools_returns_exact_allow_list_and_requires_auth():
    client = _client()
    response = client.get("/api/v1/mcp/tools", params={"agent_id": "A01"}, headers=HEADERS)
    assert response.status_code == 200
    tools = response.json()["tools"]
    assert {tool["name"] for tool in tools} == {
        "get_risk_state",
        "deploy_shadow",
        "get_policy",
    }
    assert len(tools) == 3
    for tool in tools:
        assert "scope" in tool
        assert "requires_approval" in tool
    unauthenticated = client.get("/api/v1/mcp/tools", params={"agent_id": "A01"})
    assert unauthenticated.status_code == 401


def test_call_get_risk_state_returns_current_safety_state():
    client = _client()
    response = _call(client, {"agent_id": "A01", "name": "get_risk_state"})
    assert response.status_code == 200
    assert response.json()["result"]["safety_state"] == "NORMAL"


def test_deploy_shadow_without_approval_is_refused():
    client = _client()
    response = _call(
        client,
        {
            "agent_id": "A01",
            "name": "deploy_shadow",
            "arguments": {"candidate_id": "c1"},
        },
    )
    assert response.status_code == 403
    assert "approval" in response.json()["detail"].lower()


def test_deploy_shadow_approved_without_handler_fails_closed():
    client = _client()
    response = _call(
        client,
        {
            "agent_id": "A01",
            "name": "deploy_shadow",
            "arguments": {"candidate_id": "c1"},
            "approved": True,
        },
    )
    assert response.status_code == 409


def test_unknown_tool_name_is_refused():
    client = _client()
    response = _call(client, {"agent_id": "A01", "name": "does_not_exist"})
    assert response.status_code == 403


def test_agent_cannot_call_tool_outside_its_allow_list():
    client = _client()
    response = _call(client, {"agent_id": "A02", "name": "get_risk_state"})
    assert response.status_code == 403


def test_unknown_agent_is_refused_on_list_and_call():
    client = _client()
    listed = client.get("/api/v1/mcp/tools", params={"agent_id": "A99"}, headers=HEADERS)
    assert listed.status_code == 403
    called = _call(client, {"agent_id": "A99", "name": "get_risk_state"})
    assert called.status_code == 403


def test_cost_governor_refuses_call_above_task_cap():
    client = _client()
    refused = _call(
        client,
        {
            "agent_id": "A01",
            "name": "get_risk_state",
            "estimated_tokens": 250_001,
        },
    )
    assert refused.status_code == 403
    assert "cost governor" in refused.json()["detail"].lower()
    admitted = _call(
        client,
        {
            "agent_id": "A01",
            "name": "get_risk_state",
            "estimated_tokens": 1000,
        },
    )
    assert admitted.status_code == 200


def test_default_service_lists_no_forbidden_tool_names():
    client = _default_client()
    response = client.get("/api/v1/mcp/tools", params={"agent_id": "operator"}, headers=HEADERS)
    assert response.status_code == 200
    for tool in response.json()["tools"]:
        lowered = tool["name"].lower()
        assert not any(fragment in lowered for fragment in FORBIDDEN_TOOL_FRAGMENTS)


def test_mcp_cost_governor_cap_discovery_and_check():
    assert McpCostGovernor().task_token_cap == 250_000
    custom = McpCostGovernor(BudgetHierarchy(task_token_cap=2_000))
    assert custom.task_token_cap == 2_000
    governor = McpCostGovernor()
    with pytest.raises(CostCapRefusal):
        governor.check(agent_id="A01", task_id="t1", estimated_tokens=250_001)
    governor.check(agent_id="A01", task_id="t2", estimated_tokens=100)


def test_create_app_exposes_mcp_routes():
    client = TestClient(
        create_app(
            operator_token="op",
            recovery_token="rec",
            mcp_agent_tools={"A01": {"get_risk_state"}},
        )
    )
    listed = client.get(
        "/api/v1/mcp/tools",
        params={"agent_id": "A01"},
        headers={"X-Operator-Token": "op"},
    )
    assert listed.status_code == 200
    assert [tool["name"] for tool in listed.json()["tools"]] == ["get_risk_state"]
    called = client.post(
        "/api/v1/mcp/tools/call",
        json={"agent_id": "A01", "name": "get_risk_state"},
        headers={"X-Operator-Token": "op"},
    )
    assert called.status_code == 200
    assert called.json()["result"]["safety_state"] == "NORMAL"
