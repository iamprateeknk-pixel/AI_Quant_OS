import json

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_supervisor_api import HEADERS, NOW


def test_declared_incident_review_runs_only_a18_and_retains_proposal(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        orders_before = client.get("/api/v1/orders").json()
        state_before = client.get("/api/v1/system/status").json()
        context_response = client.post(
            "/api/v1/paper/incidents/supervise/context",
            headers=HEADERS,
            json={"task_id": "incident-review"},
        )
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        assert context["agent_id"] == "A01"
        assert context["agent_version"] == "0.5.0"

        execution = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "incident-review", "context_ref": context["context_ref"]},
        )
        assert execution.status_code == 200, execution.text
        parent = execution.json()["receipt"]
        assert parent["status"] == "COMPLETED"
        plan = json.loads(parent["output_json"])
        assert plan["task_kind"] == "INCIDENT_REVIEW"
        assert plan["agent_version"] == "0.5.0"
        assert [call["agent_id"] for call in plan["calls"]] == ["A18"]
        assert plan["max_calls"] == 1
        assert plan["confidence"] == "0" and plan["requires_escalation"]
        assert plan["calls"][0]["status"] == "COMPLETED"
        assert plan["calls"][0]["agent_version"] == "0.2.0"
        assert plan["calls"][0]["output_sha256"]

        child_response = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][0]["run_id"], headers=HEADERS
        )
        assert child_response.status_code == 200
        child = child_response.json()["receipt"]
        recommendation = json.loads(child["output_json"])
        assert recommendation["agent_id"] == "A18"
        assert recommendation["workflow_dispatched"] is False
        assert recommendation["recovery_authorized"] is False
        assert (
            client.get(
                "/api/v1/paper/incidents/proposals/" + recommendation["proposal_ref"],
                headers=HEADERS,
            ).status_code
            == 200
        )
        assert client.get("/api/v1/orders").json() == orders_before
        assert client.get("/api/v1/system/status").json() == state_before

    with TestClient(create_app(**args)) as client:
        restored = client.get("/api/v1/agents/graph-runs/incident-review", headers=HEADERS)
        assert restored.status_code == 200
        assert restored.json()["receipt"] == parent


def test_incident_supervisor_route_rejects_untyped_or_unauthorized_requests(tmp_path):
    with TestClient(
        create_app(operator_token="fixture-supervisor", paper_db_path=tmp_path / "paper.sqlite3")
    ) as client:
        unauthorized = client.post(
            "/api/v1/paper/incidents/supervise/context", json={"task_id": "incident-review"}
        )
        assert unauthorized.status_code == 401
        malformed = client.post(
            "/api/v1/paper/incidents/supervise/context",
            headers=HEADERS,
            json={"task_id": "incident-review", "agents": ["A13"]},
        )
        assert malformed.status_code == 422
        assert client.get("/api/v1/agents/call-budget", headers=HEADERS).status_code == 200
        assert all(
            row["daily_calls"] == 0
            for row in client.get("/api/v1/agents/call-budget", headers=HEADERS).json()["agents"]
        )


def test_incident_plan_is_bound_to_its_task_and_exact_child(tmp_path):
    from ai_quant_os.agent_context import CompiledContext
    from ai_quant_os.task_supervisor_incident import TaskPlan

    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        context_response = client.post(
            "/api/v1/paper/incidents/supervise/context",
            headers=HEADERS,
            json={"task_id": "bound-incident-review"},
        )
        assert context_response.status_code == 200
        context_ref = context_response.json()["context_ref"]
        raw_context = client.get("/api/v1/agents/contexts/" + context_ref, headers=HEADERS).json()[
            "checkpoint"
        ]["context"]
        context = CompiledContext.model_validate(raw_context)
        execution = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "bound-incident-review", "context_ref": context_ref},
        ).json()
        output = json.loads(execution["receipt"]["output_json"])
        output["task_kind"] = "DATA_REVIEW"
        with pytest.raises(ValueError):
            TaskPlan.model_validate(output).require_context(context)
        output["task_kind"] = "INCIDENT_REVIEW"
        output["calls"][0]["agent_id"] = "A07"
        with pytest.raises(ValueError):
            TaskPlan.model_validate(output).require_context(context)


def test_incident_cohort_is_deferred_when_health_is_error():
    import asyncio

    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.supervisor_sources import supervisor_sources
    from ai_quant_os.task_supervisor_incident import read_context, wake_plan
    from ai_quant_os.world_state import WorldStateEngine

    registry = load_agent_catalog()
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    context = asyncio.run(
        ContextCompiler(
            registry,
            supervisor_sources(
                world,
                registry,
                version="0.5.0",
                task_id="unavailable-incident-review",
                task_kind="INCIDENT_REVIEW",
                health_status="ERROR",
                open_incidents=1,
            ),
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=160000,
        ).compile("A01", "0.5.0", task_id="unavailable-incident-review", scope="PAPER")
    )
    selected, deferred = wake_plan(read_context(context))
    assert selected == ()
    assert any(row.agent_id == "A18" and row.reason == "HEALTH_ERROR" for row in deferred)


def test_incident_supervisor_schemas_match_runtime():
    from pathlib import Path

    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.task_supervisor_incident import IncidentSupervisionInput, TaskPlan

    schemas = Path(__file__).resolve().parents[1] / "docs/schemas"
    for name, model in [
        ("task-plan-0.5.0", TaskPlan),
        ("incident-supervision-input-0.5.0", IncidentSupervisionInput),
    ]:
        schema = schemas / (name + ".schema.json")
        assert schema.is_file()
        assert json.loads(schema.read_text(encoding="utf-8")) == model.model_json_schema()
    assert load_agent_catalog().get("A01", "0.5.0").required_inputs[-1] == "task_spec"
