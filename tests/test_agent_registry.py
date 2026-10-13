import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.agent_permissions import READ_TOOLS, ROLE_CEILINGS, SANDBOX_TOOLS
from ai_quant_os.agent_registry import (
    AgentManifest,
    AgentRegistry,
    AgentToolBoundary,
    load_agent_catalog,
)


def test_catalog_covers_exact_pack_agents_and_reports_planned_implementations():
    registry = load_agent_catalog()
    manifests = tuple(row for row in registry.manifests() if row.agent_version == "0.1.0")
    assert [item.agent_id for item in manifests] == [f"A{index:02}" for index in range(1, 19)]
    assert all(item.implementation_status == "PLANNED" for item in manifests)
    assert all(item.agent_version == "0.1.0" for item in manifests)
    assert registry.get("A01", "0.1.0").name == "Master Supervisor"
    assert registry.get("A18", "0.1.0").squad == "Safety"
    assert registry.get("A10", "0.1.0").output_type == "ValidationScorecard"
    assert registry.get("A02", "0.2.0").implementation_status == "PARTIAL"
    assert registry.get("A11", "0.2.0").implementation_status == "PARTIAL"
    assert registry.get("A11", "0.2.0").output_type == "AdversarialScorecard"


@pytest.mark.parametrize("agent_id", [f"A{index:02}" for index in range(1, 19)])
@pytest.mark.parametrize(
    "tool",
    [
        "BROKER_SUBMIT",
        "RISK_POLICY_WRITE",
        "CREDENTIAL_READ",
        "PRODUCTION_DEPLOY",
        "SAFETY_RECOVER",
    ],
)
def test_all_agents_denied_capital_control_and_unknown_tools(agent_id, tool):
    with pytest.raises(PermissionError):
        load_agent_catalog().require_tool(agent_id, "0.1.0", tool, scope="PAPER")


def test_manifest_versions_are_immutable_and_cannot_expand_their_role_ceiling():
    registry = load_agent_catalog()
    original = registry.get("A09", "0.1.0")
    assert registry.register(original) is False
    with pytest.raises(ValueError, match="conflict"):
        registry.register(original.model_copy(update={"name": "changed"}))
    expanded = original.model_dump()
    expanded["agent_version"] = "0.2.0"
    expanded["allowed_tools"] += ("HOLDOUT_READ",)
    with pytest.raises(ValueError):
        registry.register(AgentManifest.model_validate(expanded))
    assert registry.get("A09", "0.1.0") == original


def test_generators_cannot_read_holdout_evaluation_data_and_validators_can():
    registry = load_agent_catalog()
    for agent in ("A08", "A09", "A16", "A17"):
        with pytest.raises(PermissionError):
            registry.require_tool(agent, "0.1.0", "HOLDOUT_READ", scope="SANDBOX")
    registry.require_tool("A10", "0.1.0", "HOLDOUT_READ", scope="SANDBOX")
    with pytest.raises(PermissionError):
        registry.require_tool("A10", "0.1.0", "HOLDOUT_READ", scope="PAPER")


def test_lifecycle_is_server_owned_and_live_scope_is_never_permitted():
    registry = load_agent_catalog()
    registry.require_tool("A09", "0.1.0", "CANDIDATE_CREATE", scope="SANDBOX")
    registry.set_lifecycle("A09", "0.1.0", "LIMITED")
    registry.require_tool("A09", "0.1.0", "WORLD_STATE_READ", scope="PAPER")
    with pytest.raises(PermissionError):
        registry.require_tool("A09", "0.1.0", "CANDIDATE_CREATE", scope="SANDBOX")
    registry.set_lifecycle("A09", "0.1.0", "QUARANTINED")
    with pytest.raises(PermissionError):
        registry.require_tool("A09", "0.1.0", "WORLD_STATE_READ", scope="PAPER")
    with pytest.raises(PermissionError):
        registry.require_tool("A01", "0.1.0", "WORLD_STATE_READ", scope="LIVE")
    with pytest.raises(PermissionError):
        registry.require_tool("A01", "9.9.9", "WORLD_STATE_READ", scope="PAPER")


def test_tool_boundary_denies_before_handler_and_exposes_only_installed_allowed_tools():
    calls = []

    async def read(payload):
        calls.append(payload)
        return {"fixture": "evidence"}

    registry = load_agent_catalog()
    boundary = AgentToolBoundary(registry, {"WORLD_STATE_READ": read})

    async def invoke():
        assert await boundary.call(
            "A02", "0.1.0", "WORLD_STATE_READ", scope="PAPER", payload={}
        ) == {"fixture": "evidence"}
        with pytest.raises(PermissionError):
            await boundary.call("A02", "0.1.0", "BROKER_SUBMIT", scope="PAPER", payload={})
        with pytest.raises(ValueError, match="unavailable"):
            await boundary.call("A03", "0.1.0", "MACRO_READ", scope="PAPER", payload={})
        assert len(calls) == 1

    asyncio.run(invoke())
    assert boundary.capabilities("A02", "0.1.0") == ("WORLD_STATE_READ",)
    with pytest.raises(ValueError):
        AgentToolBoundary(registry, {"BROKER_SUBMIT": read})


def test_tool_boundary_withholds_result_if_orchestration_is_revoked_during_call():
    registry = load_agent_catalog()

    async def revoke(invocation):
        registry.set_lifecycle("A01", "0.2.0", "LIMITED")
        return {"calls": []}

    async def invoke():
        boundary = AgentToolBoundary(registry, {"TASK_ORCHESTRATE": revoke})
        with pytest.raises(PermissionError):
            await boundary.call("A01", "0.2.0", "TASK_ORCHESTRATE", scope="PAPER", payload={})

    asyncio.run(invoke())


def test_registry_read_api_has_no_client_activation_or_manifest_write_route():
    client = TestClient(create_app(operator_token="fixture-token"))
    body = client.get("/api/v1/agents/registry").json()
    assert body["mode"] == "PAPER"
    assert len(body["agents"]) == 39
    assert {(item["agent_id"], item["agent_version"]) for item in body["agents"]} == (
        {(f"A{index:02}", "0.1.0") for index in range(1, 19)}
        | {
            ("A01", "0.2.0"),
            ("A01", "0.3.0"),
            ("A01", "0.4.0"),
            ("A02", "0.2.0"),
            ("A03", "0.2.0"),
            ("A04", "0.2.0"),
            ("A05", "0.2.0"),
            ("A06", "0.2.0"),
            ("A07", "0.2.0"),
            ("A08", "0.2.0"),
            ("A09", "0.2.0"),
            ("A10", "0.2.0"),
            ("A11", "0.2.0"),
            ("A12", "0.2.0"),
            ("A13", "0.2.0"),
            ("A14", "0.2.0"),
            ("A15", "0.2.0"),
            ("A16", "0.2.0"),
            ("A16", "0.3.0"),
            ("A17", "0.2.0"),
            ("A18", "0.2.0"),
        }
    )
    assert all(item["lifecycle"] == "SHADOW" for item in body["agents"])
    selector = next(
        item
        for item in body["agents"]
        if (item["agent_id"], item["agent_version"]) == ("A12", "0.2.0")
    )
    assert selector["implementation_status"] == "PARTIAL"
    assert selector["allowed_tools"] == [
        "ATTRIBUTION_READ",
        "STRATEGY_REGISTRY_READ",
        "WORLD_STATE_READ",
    ]
    assert all(
        item["implementation_status"] == "PLANNED"
        for item in body["agents"]
        if item["agent_version"] == "0.1.0"
    )
    assert (
        next(item for item in body["agents"] if item["agent_version"] == "0.2.0")[
            "implementation_status"
        ]
        == "PARTIAL"
    )
    assert (
        client.post(
            "/api/v1/agents/registry",
            headers={"X-Operator-Token": "fixture-token"},
            json={"agent_id": "A09", "lifecycle": "ACTIVE"},
        ).status_code
        == 405
    )


def test_empty_registry_and_unknown_lifecycle_fail_closed():
    registry = AgentRegistry()
    assert registry.manifests() == ()
    with pytest.raises(PermissionError):
        registry.require_tool("A01", "0.1.0", "WORLD_STATE_READ", scope="PAPER")
    with pytest.raises(ValueError):
        load_agent_catalog().set_lifecycle("A01", "0.1.0", "promoted")


def test_timeout_cancels_handler_and_exception_details_are_sanitized():
    cancelled = []

    async def slow(invocation):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(invocation.scope)

    async def fail(invocation):
        raise RuntimeError("private-handler-secret")

    registry = load_agent_catalog()
    manifest = registry.get("A02", "0.1.0").model_copy(
        update={"agent_version": "9.0.0", "timeout_seconds": 0.01}
    )
    registry.register(manifest)

    async def invoke():
        with pytest.raises(TimeoutError, match="timed out"):
            await AgentToolBoundary(registry, {"WORLD_STATE_READ": slow}).call(
                "A02", "9.0.0", "WORLD_STATE_READ", scope="PAPER", payload={}
            )
        with pytest.raises(ValueError, match="Agent tool failed") as error:
            await AgentToolBoundary(registry, {"WORLD_STATE_READ": fail}).call(
                "A02", "9.0.0", "WORLD_STATE_READ", scope="PAPER", payload={}
            )
        assert "private-handler-secret" not in str(error.value)

    asyncio.run(invoke())
    assert cancelled == ["PAPER"]


def test_payload_limits_and_scope_are_bound_before_handler():
    calls = []

    async def read(invocation):
        calls.append(invocation)
        return {"text": "x" * 65536}

    boundary = AgentToolBoundary(load_agent_catalog(), {"WORLD_STATE_READ": read})

    async def invoke():
        with pytest.raises(ValueError, match="input exceeds"):
            await boundary.call(
                "A02", "0.1.0", "WORLD_STATE_READ", scope="PAPER", payload={"text": "x" * 16384}
            )
        assert calls == []
        with pytest.raises(ValueError, match="authority fields"):
            await boundary.call(
                "A02", "0.1.0", "WORLD_STATE_READ", scope="PAPER", payload={"scope": "LIVE"}
            )
        assert calls == []
        with pytest.raises(ValueError, match="output exceeds"):
            await boundary.call("A02", "0.1.0", "WORLD_STATE_READ", scope="PAPER", payload={})
        assert calls[0].scope == "PAPER"

    asyncio.run(invoke())


def test_published_agent_permission_matrix_matches_runtime_policy():
    path = Path(__file__).resolve().parents[1] / "docs" / "agent-permission-matrix.json"
    matrix = json.loads(path.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "1.0"
    assert matrix["agent_capabilities"] == {
        agent: sorted(tools) for agent, tools in sorted(ROLE_CEILINGS.items())
    }
    assert matrix["read_tools"] == sorted(READ_TOOLS)
    assert matrix["sandbox_only_tools"] == sorted(SANDBOX_TOOLS)
    assert matrix["allowed_scopes"] == ["PAPER", "SANDBOX"]
    assert matrix["live_scope_granted"] is False
    assert matrix["deny_by_default"] is True
