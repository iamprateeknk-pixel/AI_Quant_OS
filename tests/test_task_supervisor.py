import json

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_supervisor_api import NOW, HEADERS


def task_run(client, kind, identity="task-review"):
    response = client.post(
        "/api/v1/paper/tasks/supervise/context",
        headers=HEADERS,
        json={"task_id": identity, "task_kind": kind},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    result = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": identity, "context_ref": context["context_ref"]},
    )
    assert result.status_code == 200, result.text
    receipt = result.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return context, receipt, json.loads(receipt["output_json"])


@pytest.mark.parametrize(
    "kind,agents",
    [
        ("DATA_REVIEW", ["A07"]),
        ("EVENT_REVIEW", ["A06"]),
        ("DRIFT_REVIEW", ["A14"]),
        ("MARKET_REVIEW", ["A07", "A06", "A05", "A02", "A04", "A03"]),
    ],
)
def test_declared_task_invokes_only_its_required_child_graphs(tmp_path, kind, agents):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        before = client.get("/api/v1/orders").json()
        context, receipt, plan = task_run(client, kind)
        assert context["agent_version"] == plan["agent_version"] == "0.4.0"
        assert plan["task_kind"] == kind and plan["max_calls"] == len(agents)
        assert [row["agent_id"] for row in plan["calls"]] == agents
        retry = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "second-parent", "context_ref": context["context_ref"]},
        ).json()["receipt"]
        assert retry["status"] == "COMPLETED"
        assert json.loads(retry["output_json"])["calls"] == plan["calls"]
        for row in plan["calls"]:
            child = client.get("/api/v1/agents/graph-runs/" + row["run_id"], headers=HEADERS)
            assert child.status_code == 200
            assert child.json()["receipt"]["task_id"] == "task-review"
        assert any(row["reason"] == "NOT_REQUESTED" for row in plan["deferred"])
        assert plan["requires_escalation"] and plan["confidence"] == "0"
        assert client.get("/api/v1/orders").json() == before
    with TestClient(create_app(**args)) as client:
        assert (
            client.get("/api/v1/agents/graph-runs/task-review", headers=HEADERS).json()["receipt"]
            == receipt
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "invalid"},
        {"task_id": "invalid", "task_kind": "EXECUTE_TRADES"},
        {"task_id": "invalid", "task_kind": "DATA_REVIEW", "agents": ["A12"]},
        {"task_id": "invalid", "task_kind": "DATA_REVIEW", "max_calls": 18},
        {"task_id": "invalid", "task_kind": "DATA_REVIEW", "tools": ["BROKER_SUBMIT"]},
    ],
)
def test_task_intent_cannot_supply_authority(tmp_path, payload):
    from ai_quant_os.agent_call_budgets import AgentCallPolicy, AgentCallRule

    policy = AgentCallPolicy(tuple(AgentCallRule(f"A{i:02}", 20, 100, 0, 0) for i in range(1, 19)))
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            agent_call_policy=policy,
        )
    ) as client:
        assert (
            client.post(
                "/api/v1/paper/tasks/supervise/context", headers=HEADERS, json=payload
            ).status_code
            == 422
        )
        rows = client.get("/api/v1/agents/call-budget", headers=HEADERS).json()["agents"]
        assert all(row["daily_calls"] == 0 for row in rows)


def test_task_route_requires_owner_and_typed_declaration(tmp_path):
    with TestClient(
        create_app(operator_token="fixture-supervisor", paper_db_path=tmp_path / "paper.sqlite3")
    ) as client:
        assert (
            client.post(
                "/api/v1/paper/tasks/supervise/context",
                json={"task_id": "test", "task_kind": "DATA_REVIEW"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/paper/agents/context",
                headers=HEADERS,
                json={"agent_id": "A01", "agent_version": "0.4.0", "task_id": "test"},
            ).status_code
            == 422
        )


def test_only_requested_child_consumes_quota_and_retry_reuses_receipt(tmp_path):
    from ai_quant_os.agent_call_budgets import AgentCallPolicy, AgentCallRule

    policy = AgentCallPolicy(tuple(AgentCallRule(f"A{i:02}", 20, 100, 0, 0) for i in range(1, 19)))
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            agent_call_policy=policy,
        )
    ) as client:
        context, _, plan = task_run(client, "DRIFT_REVIEW")

        def counts():
            return {
                row["agent_id"]: row["daily_calls"]
                for row in client.get("/api/v1/agents/call-budget", headers=HEADERS).json()[
                    "agents"
                ]
            }

        expected = {f"A{i:02}": 0 for i in range(1, 19)}
        expected.update(A01=6, A14=5)
        assert counts() == expected
        receipt = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "repeat", "context_ref": context["context_ref"]},
        ).json()["receipt"]
        assert json.loads(receipt["output_json"])["calls"] == plan["calls"]
        expected["A01"] += 1
        assert counts() == expected


@pytest.mark.parametrize("mutation", ["omit", "kind", "cap", "defer"])
def test_plan_cannot_change_original_task_selection(tmp_path, mutation):
    from ai_quant_os.agent_context import CompiledContext
    from ai_quant_os.task_supervisor import TaskPlan

    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        raw_context, _, raw = task_run(client, "DRIFT_REVIEW")
        context = CompiledContext.model_validate(
            client.get(
                "/api/v1/agents/contexts/" + raw_context["context_ref"], headers=HEADERS
            ).json()["checkpoint"]["context"]
        )
        if mutation == "omit":
            raw["selected"], raw["calls"] = [], []
        elif mutation == "kind":
            raw["task_kind"] = "DATA_REVIEW"
        elif mutation == "cap":
            raw["max_calls"] = 7
        else:
            raw["deferred"] = []
        with pytest.raises(ValueError):
            TaskPlan.model_validate(raw).require_context(context)


def test_schema_exports_match_runtime():
    from pathlib import Path
    from ai_quant_os.task_supervisor import TaskPlan, TaskSupervisionInput

    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    for name, model in [
        ("task-plan-0.4.0", TaskPlan),
        ("task-supervision-input", TaskSupervisionInput),
    ]:
        assert (
            json.loads((root / (name + ".schema.json")).read_text(encoding="utf-8"))
            == model.model_json_schema()
        )


@pytest.mark.parametrize("state", ["before", "after", "health"])
def test_requested_child_respects_quarantine_and_health(tmp_path, state):
    app = create_app(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(app) as client:
        registry = app.state.graph_runtime._registry
        if state == "before":
            registry.quarantine("A14", "0.2.0", registry.get("A14", "0.2.0").manifest_ref)
        response = client.post(
            "/api/v1/paper/tasks/supervise/context",
            headers=HEADERS,
            json={"task_id": "blocked", "task_kind": "DRIFT_REVIEW"},
        )
        assert response.status_code == 200
        if state == "after":
            registry.quarantine("A14", "0.2.0", registry.get("A14", "0.2.0").manifest_ref)
        if state == "health":
            # Test the same immutable source/compiler boundary with explicit error evidence.
            import asyncio
            from ai_quant_os.agent_context import ContextCompiler
            from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
            from ai_quant_os.world_state import WorldStateEngine
            from ai_quant_os.supervisor_sources import supervisor_sources
            from ai_quant_os.task_supervisor import read_context, wake_plan

            world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
            context = asyncio.run(
                ContextCompiler(
                    registry,
                    supervisor_sources(
                        world,
                        registry,
                        version="0.4.0",
                        task_id="blocked",
                        task_kind="DRIFT_REVIEW",
                        health_status="ERROR",
                        open_incidents=1,
                    ),
                    clock=lambda: NOW,
                    max_input_bytes=65536,
                    max_context_bytes=160000,
                ).compile("A01", "0.4.0", task_id="blocked", scope="PAPER")
            )
            selected, deferred = wake_plan(read_context(context))
            assert selected == ()
            assert any(row.agent_id == "A14" and row.reason == "HEALTH_ERROR" for row in deferred)
            return
        receipt = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "blocked", "context_ref": response.json()["context_ref"]},
        ).json()["receipt"]
        assert receipt["status"] == "COMPLETED"
        plan = json.loads(receipt["output_json"])
        if state == "before":
            assert plan["calls"] == []
            assert any(
                row["agent_id"] == "A14" and row["reason"] == "QUARANTINED"
                for row in plan["deferred"]
            )
        else:
            assert plan["calls"][0]["status"] == "NOT_STARTED_PERMISSION"


@pytest.mark.parametrize("field", ["task_id", "world_state_version", "as_of"])
def test_declaration_is_bound_to_original_context(field):
    import asyncio
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.supervisor_sources import supervisor_sources
    from ai_quant_os.task_supervisor import read_context, TaskSupervisorEvidence

    registry = load_agent_catalog()
    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    context = asyncio.run(
        ContextCompiler(
            registry,
            supervisor_sources(
                world, registry, version="0.4.0", task_id="binding", task_kind="DATA_REVIEW"
            ),
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=160000,
        ).compile("A01", "0.4.0", task_id="binding", scope="PAPER")
    )
    raw = read_context(context).model_dump(mode="json")
    if field == "task_id":
        with pytest.raises(ValueError):
            read_context(context.model_copy(update={"task_id": "another"}))
    else:
        raw["task_spec"][field] = (
            "world:" + "0" * 64 if field == "world_state_version" else "2026-10-02T12:00:00Z"
        )
        with pytest.raises(ValueError):
            TaskSupervisorEvidence.model_validate(raw)


def test_same_task_id_with_different_intent_has_separate_receipts(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        first, _, a = task_run(client, "DATA_REVIEW", "same-task")
        second = client.post(
            "/api/v1/paper/tasks/supervise/context",
            headers=HEADERS,
            json={"task_id": "same-task", "task_kind": "EVENT_REVIEW"},
        ).json()
        assert first["context_ref"] != second["context_ref"]
        receipt = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "second-purpose", "context_ref": second["context_ref"]},
        ).json()["receipt"]
        assert receipt["status"] == "COMPLETED"
        b = json.loads(receipt["output_json"])
        assert b["calls"][0]["agent_id"] == "A06"
        assert b["calls"][0]["run_id"] != a["calls"][0]["run_id"]
