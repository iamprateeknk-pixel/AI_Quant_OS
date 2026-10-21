import json

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from test_research_hypothesis_api import HEADERS, NOW, seed

pytest_plugins = ("test_strategy_prefix_worker",)


def test_research_supervisor_dispatches_only_a08_in_sandbox_and_survives_restart(tmp_path):
    args = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        seed(client)
        orders_before = client.get("/api/v1/orders").json()
        state_before = client.get("/api/v1/system/status").json()
        context_response = client.post(
            "/api/v1/sandbox/research/supervise/context",
            headers=HEADERS,
            json={"task_id": "supervised-research"},
        )
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        assert (context["agent_id"], context["agent_version"], context["scope"]) == (
            "A01",
            "0.6.0",
            "SANDBOX",
        )

        execution = client.post(
            "/api/v1/sandbox/research/supervise/execute",
            headers=HEADERS,
            json={"run_id": "supervised-research", "context_ref": context["context_ref"]},
        )
        assert execution.status_code == 200, execution.text
        parent = execution.json()["receipt"]
        plan = json.loads(parent["output_json"])
        assert parent["status"] == "COMPLETED"
        assert plan["task_kind"] == "HYPOTHESIS_RESEARCH"
        assert plan["agent_version"] == "0.6.0"
        assert [row["agent_id"] for row in plan["calls"]] == ["A08"]
        assert plan["max_calls"] == 1
        assert plan["calls"][0]["status"] == "COMPLETED"
        assert plan["calls"][0]["agent_version"] == "0.2.0"

        child = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][0]["run_id"], headers=HEADERS
        ).json()["receipt"]
        assert (child["agent_id"], child["status"]) == ("A08", "COMPLETED")
        child_context = client.get(
            "/api/v1/agents/contexts/" + child["context_ref"], headers=HEADERS
        ).json()["checkpoint"]["context"]
        assert (child_context["agent_id"], child_context["scope"]) == ("A08", "SANDBOX")
        output = json.loads(child["output_json"])
        assert output["status"] == "PROPOSED"
        assert output["execution_authorized"] is False
        assert client.get("/api/v1/orders").json() == orders_before
        assert client.get("/api/v1/system/status").json() == state_before

        paper_execution = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "paper-cross-scope", "context_ref": context["context_ref"]},
        )
        assert paper_execution.status_code == 403

    with TestClient(create_app(**args)) as client:
        restored = client.get("/api/v1/agents/graph-runs/supervised-research", headers=HEADERS)
        assert restored.status_code == 200
        assert restored.json()["receipt"] == parent


def test_versioned_research_supervisor_dispatches_a08_v3_and_survives_restart(tmp_path):
    args = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        seed(client)
        before = client.get("/api/v1/system/status").json()
        context_response = client.post(
            "/api/v1/sandbox/research/supervise/v3/context",
            headers=HEADERS,
            json={"task_id": "supervised-research-v3"},
        )
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        assert (context["agent_id"], context["agent_version"], context["scope"]) == (
            "A01",
            "0.7.0",
            "SANDBOX",
        )

        execution = client.post(
            "/api/v1/sandbox/research/supervise/v3/execute",
            headers=HEADERS,
            json={"run_id": "supervised-research-v3", "context_ref": context["context_ref"]},
        )
        assert execution.status_code == 200, execution.text
        parent = execution.json()["receipt"]
        assert parent["status"] == "COMPLETED", parent
        plan = json.loads(parent["output_json"])
        assert plan["agent_version"] == "0.7.0"
        assert [row["agent_id"] for row in plan["calls"]] == ["A08"]
        assert plan["calls"][0]["agent_version"] == "0.3.0"
        child = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][0]["run_id"], headers=HEADERS
        ).json()["receipt"]
        output = json.loads(child["output_json"])
        assert output["novelty_scope"] == "COMPLETED_AND_ACTIVE_RESERVATIONS_AT_CAPTURE"
        assert output["execution_authorized"] is False
        assert client.get("/api/v1/system/status").json() == before
        assert (
            client.post(
                "/api/v1/paper/agents/context",
                headers=HEADERS,
                json={"agent_id": "A01", "agent_version": "0.7.0", "task_id": "wrong-scope"},
            ).status_code
            == 422
        )

    with TestClient(create_app(**args)) as client:
        restored = client.get("/api/v1/agents/graph-runs/supervised-research-v3", headers=HEADERS)
        assert restored.status_code == 200
        assert restored.json()["receipt"] == parent


def test_research_supervisor_route_is_typed_operator_only_and_fixed_to_a08(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert (
            client.post(
                "/api/v1/sandbox/research/supervise/context", json={"task_id": "missing-auth"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/sandbox/research/supervise/v3/context",
                json={"task_id": "missing-auth"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/sandbox/research/supervise/v4/context",
                json={
                    "task_id": "missing-auth-v4",
                    "artifact_ref": "candidate-validation:" + "a" * 64,
                },
            ).status_code
            == 401
        )
        v3_malformed = client.post(
            "/api/v1/sandbox/research/supervise/v3/context",
            headers=HEADERS,
            json={"task_id": "fixed-cohort-v3", "agent_ids": ["A09"]},
        )
        assert v3_malformed.status_code == 422
        v4_malformed = client.post(
            "/api/v1/sandbox/research/supervise/v4/context",
            headers=HEADERS,
            json={
                "task_id": "fixed-cohort-v4",
                "artifact_ref": "candidate-validation:" + "a" * 64,
                "agent_ids": ["A09"],
            },
        )
        assert v4_malformed.status_code == 422
        malformed = client.post(
            "/api/v1/sandbox/research/supervise/context",
            headers=HEADERS,
            json={"task_id": "fixed-cohort", "agent_ids": ["A09"]},
        )
        assert malformed.status_code == 422
        paper_route = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A01", "agent_version": "0.6.0", "task_id": "wrong-scope"},
        )
        assert paper_route.status_code == 422
        paper_route_v4 = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A01", "agent_version": "0.8.0", "task_id": "wrong-scope"},
        )
        assert paper_route_v4.status_code == 422


def test_research_supervisor_schemas_and_registry_match_runtime():
    from pathlib import Path

    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.task_supervisor_research import (
        ResearchSupervisionInput,
        ValidationResearchSupervisionInput,
        ConstructionResearchSupervisionInput,
        TaskPlan,
        TaskPlanV3,
        TaskPlanV4,
        TaskPlanV5,
    )

    schemas = Path(__file__).resolve().parents[1] / "docs/schemas"
    for name, model in [
        ("task-plan-0.6.0", TaskPlan),
        ("research-supervision-input-0.6.0", ResearchSupervisionInput),
        ("task-plan-0.7.0", TaskPlanV3),
        ("research-supervision-input-0.7.0", ResearchSupervisionInput),
        ("task-plan-0.8.0", TaskPlanV4),
        ("research-supervision-input-0.8.0", ValidationResearchSupervisionInput),
        ("task-plan-0.9.0", TaskPlanV5),
        ("research-supervision-input-0.9.0", ConstructionResearchSupervisionInput),
    ]:
        assert json.loads((schemas / f"{name}.schema.json").read_text(encoding="utf-8")) == (
            model.model_json_schema()
        )
    manifest = load_agent_catalog().get("A01", "0.6.0")
    assert "TASK_ORCHESTRATE" in manifest.allowed_tools
    assert manifest.implementation_status == "PARTIAL"
    manifest_v3 = load_agent_catalog().get("A01", "0.7.0")
    assert manifest_v3.output_type == "TaskPlanV3"
    assert manifest_v3.implementation_status == "PARTIAL"
    manifest_v4 = load_agent_catalog().get("A01", "0.8.0")
    assert manifest_v4.output_type == "TaskPlanV4"
    assert manifest_v4.implementation_status == "PARTIAL"
    manifest_v5 = load_agent_catalog().get("A01", "0.9.0")
    assert manifest_v5.output_type == "TaskPlanV5"
    assert manifest_v5.implementation_status == "PARTIAL"


def test_research_supervisor_plan_is_bound_to_exact_sandbox_child(tmp_path):
    from ai_quant_os.agent_context import CompiledContext
    from ai_quant_os.task_supervisor_research import TaskPlan

    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        seed(client)
        context_response = client.post(
            "/api/v1/sandbox/research/supervise/context",
            headers=HEADERS,
            json={"task_id": "bound-supervised-research"},
        )
        assert context_response.status_code == 200
        context_ref = context_response.json()["context_ref"]
        raw = client.get("/api/v1/agents/contexts/" + context_ref, headers=HEADERS).json()
        context = CompiledContext.model_validate(raw["checkpoint"]["context"])
        result = client.post(
            "/api/v1/sandbox/research/supervise/execute",
            headers=HEADERS,
            json={"run_id": "bound-supervised-research", "context_ref": context_ref},
        )
        output = json.loads(result.json()["receipt"]["output_json"])
        output["calls"][0]["agent_id"] = "A09"
        with pytest.raises(ValueError):
            TaskPlan.model_validate(output).require_context(context)


def test_validation_review_supervisor_dispatches_only_a10_for_selected_archive(package, tmp_path):
    from test_candidate_validation_store import failed_tests
    from ai_quant_os.candidate_validation_store import CandidateValidationStore

    with CandidateValidationStore(tmp_path / "paper.candidate-validations.sqlite3") as archive:
        failure = archive.put_failure(package, failed_tests(package))
    args = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        before_orders = client.get("/api/v1/orders").json()
        before_state = client.get("/api/v1/system/status").json()
        compiled = client.post(
            "/api/v1/sandbox/research/supervise/v4/context",
            headers=HEADERS,
            json={"task_id": "review-archive", "artifact_ref": failure.failure_ref},
        )
        assert compiled.status_code == 200, compiled.text
        context = compiled.json()
        assert (context["agent_id"], context["agent_version"], context["scope"]) == (
            "A01",
            "0.8.0",
            "SANDBOX",
        )
        assert {row["input_name"] for row in context["inputs"]} == {
            "agent_registry",
            "world_state",
            "health",
            "permissions",
            "task_spec",
        }
        task_spec = next(row for row in context["inputs"] if row["input_name"] == "task_spec")
        assert json.loads(task_spec["payload_json"])["artifact_ref"] == failure.failure_ref
        assert all(
            package.holdout.receipt.dataset_ref not in row["payload_json"]
            and "holdout_ref" not in row["payload_json"]
            for row in context["inputs"]
        )
        executed = client.post(
            "/api/v1/sandbox/research/supervise/v4/execute",
            headers=HEADERS,
            json={"run_id": "review-archive", "context_ref": context["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        parent = executed.json()["receipt"]
        plan = json.loads(parent["output_json"])
        assert plan["agent_version"] == "0.8.0"
        assert plan["task_kind"] == "VALIDATION_REVIEW"
        assert [(row["agent_id"], row["agent_version"]) for row in plan["calls"]] == [
            ("A10", "0.2.0")
        ]
        assert plan["calls"][0]["status"] == "COMPLETED"
        child = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][0]["run_id"], headers=HEADERS
        ).json()["receipt"]
        summary = json.loads(child["output_json"])["summary"]
        assert summary["artifact_ref"] == failure.failure_ref
        assert summary["outcome"] == "PREREQUISITE_FAILED"
        assert client.get("/api/v1/orders").json() == before_orders
        assert client.get("/api/v1/system/status").json() == before_state

    with TestClient(create_app(**args)) as client:
        restored = client.get("/api/v1/agents/graph-runs/review-archive", headers=HEADERS)
        assert restored.status_code == 200
        assert restored.json()["receipt"] == parent


def test_research_supervisor_defers_a08_on_health_error():
    import asyncio

    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.supervisor_sources import supervisor_sources
    from ai_quant_os.task_supervisor_research import read_context, wake_plan
    from ai_quant_os.world_state import WorldStateEngine

    registry = load_agent_catalog()
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    context = asyncio.run(
        ContextCompiler(
            registry,
            supervisor_sources(
                world,
                registry,
                version="0.6.0",
                task_id="unavailable-research",
                task_kind="HYPOTHESIS_RESEARCH",
                health_status="ERROR",
                open_incidents=1,
            ),
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=160000,
        ).compile("A01", "0.6.0", task_id="unavailable-research", scope="SANDBOX")
    )
    selected, deferred = wake_plan(read_context(context))
    assert selected == ()
    assert any(row.agent_id == "A08" and row.reason == "HEALTH_ERROR" for row in deferred)


def test_reviewed_construction_supervisor_dispatches_only_a09_and_restarts(tmp_path):
    from test_strategy_builder_api import review

    args = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        reviewed = review(client)
        inactive = client.post(
            "/api/v1/sandbox/research/supervise/v5/context",
            headers=HEADERS,
            json={"task_id": "inactive-review", "review_ref": "hypothesis-review:" + "0" * 64},
        )
        assert inactive.status_code == 404
        malformed = client.post(
            "/api/v1/sandbox/research/supervise/v5/context",
            headers=HEADERS,
            json={
                "task_id": "extra-input",
                "review_ref": reviewed["review_ref"],
                "hypothesis_text": "must not be accepted",
            },
        )
        assert malformed.status_code == 422
        before_orders = client.get("/api/v1/orders").json()
        before_state = client.get("/api/v1/system/status").json()
        compiled = client.post(
            "/api/v1/sandbox/research/supervise/v5/context",
            headers=HEADERS,
            json={"task_id": "supervised-construction", "review_ref": reviewed["review_ref"]},
        )
        assert compiled.status_code == 200, compiled.text
        context = compiled.json()
        assert (context["agent_id"], context["agent_version"], context["scope"]) == (
            "A01",
            "0.9.0",
            "SANDBOX",
        )
        assert {row["input_name"] for row in context["inputs"]} == {
            "agent_registry",
            "world_state",
            "health",
            "permissions",
            "task_spec",
        }
        task_spec = next(row for row in context["inputs"] if row["input_name"] == "task_spec")
        declaration = json.loads(task_spec["payload_json"])
        assert declaration["review_ref"] == reviewed["review_ref"]
        assert "hypothesis_spec" not in task_spec["payload_json"].lower()
        assert "hypothesis_text" not in task_spec["payload_json"].lower()

        executed = client.post(
            "/api/v1/sandbox/research/supervise/v5/execute",
            headers=HEADERS,
            json={"run_id": "supervised-construction", "context_ref": context["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        parent = executed.json()["receipt"]
        plan = json.loads(parent["output_json"])
        assert plan["agent_version"] == "0.9.0"
        assert plan["task_kind"] == "STRATEGY_CONSTRUCTION"
        assert [(row["agent_id"], row["agent_version"]) for row in plan["calls"]] == [
            ("A09", "0.2.0")
        ]
        assert plan["calls"][0]["status"] == "COMPLETED"
        child = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][0]["run_id"], headers=HEADERS
        ).json()["receipt"]
        child_output = json.loads(child["output_json"])
        assert child_output["agent_id"] == "A09"
        assert child_output["execution_authorized"] is False
        assert child_output["promotion_authorized"] is False
        candidate = client.get(
            "/api/v1/sandbox/strategy-builder/candidates/" + child_output["candidate_ref"],
            headers=HEADERS,
        )
        assert candidate.status_code == 200, candidate.text
        assert candidate.json()["candidate"]["review"]["review_id"] == reviewed["review_id"]
        assert client.get("/api/v1/orders").json() == before_orders
        assert client.get("/api/v1/system/status").json() == before_state
        assert (
            client.post(
                "/api/v1/paper/agents/context",
                headers=HEADERS,
                json={"agent_id": "A01", "agent_version": "0.9.0", "task_id": "wrong-scope"},
            ).status_code
            == 422
        )

    with TestClient(create_app(**args)) as client:
        restored = client.get("/api/v1/agents/graph-runs/supervised-construction", headers=HEADERS)
        assert restored.status_code == 200
        assert restored.json()["receipt"] == parent
