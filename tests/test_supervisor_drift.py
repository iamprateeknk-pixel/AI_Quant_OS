import json

import pytest

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_supervisor_api import NOW, HEADERS


def run(client, run_id="supervisor-drift", version="0.3.0"):
    context = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A01", "agent_version": version, "task_id": run_id},
    )
    assert context.status_code == 200, context.text
    result = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": run_id, "context_ref": context.json()["context_ref"]},
    )
    assert result.status_code == 200, result.text
    receipt = result.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return receipt, json.loads(receipt["output_json"])


def test_new_supervisor_executes_source_bound_drift_without_capital_changes(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        before = client.get("/api/v1/orders").json()
        receipt, output = run(client)
        assert output["agent_version"] == "0.3.0"
        assert output["max_calls"] == 7
        assert [row["agent_id"] for row in output["calls"]] == [
            "A07",
            "A06",
            "A05",
            "A02",
            "A04",
            "A03",
            "A14",
        ]
        drift = output["calls"][-1]
        assert drift["status"] == "COMPLETED"
        child = client.get("/api/v1/agents/graph-runs/" + drift["run_id"], headers=HEADERS).json()[
            "receipt"
        ]
        evidence = json.loads(child["output_json"])
        assert evidence["report"]["state"] == "POLICY_UNCONFIGURED"
        assert evidence["may_add_risk"] is False
        assert evidence["confidence"] == "0"
        assert client.get("/api/v1/orders").json() == before
        assert output["requires_escalation"] and output["confidence"] == "0"
    with TestClient(create_app(**args)) as client:
        stored = client.get("/api/v1/agents/graph-runs/supervisor-drift", headers=HEADERS)
        assert stored.json()["receipt"] == receipt
        assert (
            client.get("/api/v1/agents/graph-runs/" + drift["run_id"], headers=HEADERS).json()[
                "receipt"
            ]
            == child
        )


def test_original_supervisor_still_calls_six_and_defers_drift(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, output = run(client, version="0.2.0")
        assert len(output["calls"]) == 6
        assert any(
            row["agent_id"] == "A14"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )


@pytest.mark.parametrize("when", ["before", "after"])
def test_quarantined_drift_is_not_started(tmp_path, when):
    app = create_app(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(app) as client:
        registry = app.state.graph_runtime._registry
        if when == "before":
            registry.set_lifecycle("A14", "0.2.0", "QUARANTINED")
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A01", "agent_version": "0.3.0", "task_id": "quarantine"},
        ).json()
        registry.set_lifecycle("A14", "0.2.0", "QUARANTINED")
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "quarantine", "context_ref": context["context_ref"]},
        ).json()["receipt"]
        assert result["status"] == "COMPLETED", result
        plan = json.loads(result["output_json"])
        if when == "before":
            assert len(plan["calls"]) == 6
            assert any(
                row["agent_id"] == "A14" and row["reason"] == "QUARANTINED"
                for row in plan["deferred"]
            )
        else:
            assert plan["calls"][-1]["status"] == "NOT_STARTED_PERMISSION"
        from ai_quant_os.supervisor_drift import child_run_id

        assert (
            client.get(
                "/api/v1/agents/graph-runs/" + child_run_id(context["context_ref"], "A14"),
                headers=HEADERS,
            ).status_code
            == 404
        )
        assert client.get("/api/v1/orders").json()["orders"] == []


def test_drift_context_fetches_share_durable_budget_and_do_not_repeat_on_retry(tmp_path):
    from ai_quant_os.agent_call_budgets import AgentCallPolicy, AgentCallRule

    policy = AgentCallPolicy(tuple(AgentCallRule(f"A{i:02}", 20, 100, 0, 0) for i in range(1, 19)))
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
        agent_call_policy=policy,
    )
    with TestClient(create_app(**args)) as client:
        receipt, plan = run(client)
        rows = client.get("/api/v1/agents/call-budget", headers=HEADERS).json()["agents"]
        drift = next(row for row in rows if row["agent_id"] == "A14")
        assert drift["daily_calls"] == 5
        # Another parent run on the same context reuses all child receipts.
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "another-parent", "context_ref": receipt["context_ref"]},
        ).json()["receipt"]
        assert result["status"] == "COMPLETED"
        assert json.loads(result["output_json"])["calls"] == plan["calls"]
        rows = client.get("/api/v1/agents/call-budget", headers=HEADERS).json()["agents"]
        assert next(row for row in rows if row["agent_id"] == "A14")["daily_calls"] == 5


def test_drift_quota_denial_returns_missing_child_evidence_without_dispatch(tmp_path, monkeypatch):
    from ai_quant_os.agent_call_budgets import AgentCallPolicy, AgentCallRule
    from ai_quant_os import drift_agent

    calls = []

    async def forbidden(invocation):
        calls.append(True)
        raise AssertionError("Denied drift dispatched")

    monkeypatch.setattr(drift_agent, "assess_drift", forbidden)
    policy = AgentCallPolicy(
        tuple(AgentCallRule(f"A{i:02}", 0 if i == 14 else 20, 100, 0, 0) for i in range(1, 19))
    )
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            agent_call_policy=policy,
        )
    ) as client:
        _, plan = run(client)
        assert plan["calls"][-1]["status"] == "WAIT_FOR_DATA"
        assert plan["calls"][-1]["output_sha256"] is None
        assert "A14_WAIT_FOR_DATA" in plan["contradictions"]
        assert calls == []
        assert client.get("/api/v1/orders").json()["orders"] == []


@pytest.mark.parametrize("kind,status", [("failed", "FAILED"), ("timeout", "TIMEOUT")])
def test_drift_child_failure_stays_typed_and_does_not_authorize_capital(
    tmp_path, monkeypatch, kind, status
):
    import asyncio
    from ai_quant_os import drift_agent

    async def malfunction(invocation):
        if kind == "timeout":
            await asyncio.sleep(3)
        raise RuntimeError("private child failure")

    monkeypatch.setattr(drift_agent, "assess_drift", malfunction)
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, plan = run(client)
        assert plan["calls"][-1]["status"] == status
        assert plan["calls"][-1]["output_sha256"] is None
        assert "A14_" + status in plan["contradictions"]
        assert "private child failure" not in json.dumps(plan)
        assert plan["requires_escalation"]
        assert client.get("/api/v1/orders").json()["orders"] == []


def test_new_supervisor_observes_actual_fill_and_withholds_corrupt_source(tmp_path):
    import sqlite3
    from decimal import Decimal
    from ai_quant_os.execution_drift import DriftPolicy
    from test_execution_attribution import api_account
    from test_paper_attribution import NOW as LEDGER_NOW

    seed, headers = api_account(tmp_path)
    seed.close()
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: LEDGER_NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            drift_policy=DriftPolicy(3600, 2, Decimal(10)),
        )
    ) as client:
        _, plan = run(client)
        child = client.get(
            "/api/v1/agents/graph-runs/" + plan["calls"][-1]["run_id"], headers=HEADERS
        ).json()["receipt"]
        evidence = json.loads(child["output_json"])
        row = evidence["report"]["assessments"][0]
        assert row["current"]["fill_count"] == row["current"]["benchmark_fill_count"] == 1
        assert row["state"] == "UNASSESSED" and row["cost_delta_bps"] is None
        with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
            db.execute("UPDATE paper_price_benchmarks SET benchmark_json='{}'")
        # A fresh parent can inspect catalog/market evidence; damaged child sources are withheld.
        _, corrupt = run(client, run_id="corrupt-parent")
        assert corrupt["calls"][-1]["status"] == "NOT_STARTED_SOURCE"
        assert corrupt["calls"][-1]["output_sha256"] is None


@pytest.mark.parametrize("mutation", ["omit", "version", "hash", "parent"])
def test_new_typed_plan_refuses_source_selection_and_identity_forgery(tmp_path, mutation):
    from ai_quant_os.agent_context import CompiledContext
    from ai_quant_os.supervisor_drift import TaskPlan

    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        receipt, raw = run(client)
        context = CompiledContext.model_validate(
            client.get("/api/v1/agents/contexts/" + receipt["context_ref"], headers=HEADERS).json()[
                "checkpoint"
            ]["context"]
        )
        if mutation == "omit":
            raw["selected"], raw["calls"] = raw["selected"][:-1], raw["calls"][:-1]
        elif mutation == "version":
            raw["calls"][-1]["agent_version"] = "0.1.0"
        elif mutation == "hash":
            raw["calls"][-1]["output_sha256"] = None
        else:
            raw["parent_context_ref"] = "agent-context:" + "0" * 64
        with pytest.raises(ValueError):
            TaskPlan.model_validate(raw).require_context(context)


def test_drift_supervisor_parent_expiry_stops_before_a14_source_capture(tmp_path):
    import asyncio
    from datetime import timedelta
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import load_agent_catalog, AgentToolBoundary
    from ai_quant_os.graph_runtime import GraphRuntime
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.supervisor_sources import supervisor_sources
    from ai_quant_os.supervisor_drift import supervisor_drift_implementation
    from ai_quant_os.supervisor_dispatch import SupervisorDispatch

    async def invoke():
        registry = load_agent_catalog()
        moment = [NOW]
        world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
        context = await ContextCompiler(
            registry,
            supervisor_sources(world, registry, version="0.3.0"),
            clock=lambda: moment[0],
            max_input_bytes=65536,
            max_context_bytes=160000,
        ).compile("A01", "0.3.0", task_id="deadline", scope="PAPER")
        holder, captures = [], []

        async def compile_child(agent, task):
            captures.append(agent)
            if agent == "A03":
                moment[0] += timedelta(seconds=6)
            raise ValueError("source fixture unavailable")

        async def admit():
            pass

        dispatch = SupervisorDispatch(
            registry,
            runtime=lambda: holder[0],
            context=lambda ref: context,
            compile_child=compile_child,
            admission=admit,
        )
        async with GraphRuntime.open(
            tmp_path / "graphs.sqlite3",
            registry=registry,
            implementations={("A01", "0.3.0"): supervisor_drift_implementation(registry)},
            tools=AgentToolBoundary(registry, {"TASK_ORCHESTRATE": dispatch}),
            clock=lambda: moment[0],
        ) as runtime:
            holder.append(runtime)
            receipt = await runtime.run("expired-parent", context)
            assert receipt.status == "EXPIRED" and receipt.output_json is None
            assert captures == ["A07", "A06", "A05", "A02", "A04", "A03"]

    asyncio.run(invoke())


def test_new_context_catalog_does_not_allow_client_forged_executables():
    import asyncio
    from ai_quant_os.agent_registry import load_agent_catalog
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.supervisor_sources import supervisor_sources
    from ai_quant_os.supervisor_drift import DriftSupervisorEvidence

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    bindings = supervisor_sources(world, load_agent_catalog(), version="0.3.0")
    raw = {
        name: json.loads(asyncio.run(binding.fetch()).payload_json)
        for name, binding in bindings.items()
    }
    DriftSupervisorEvidence.model_validate(raw)
    row = next(
        row
        for row in raw["agent_registry"]["entries"]
        if row["manifest"]["agent_id"] == "A18" and row["manifest"]["agent_version"] == "0.2.0"
    )
    row["executable"] = True
    with pytest.raises(ValueError):
        DriftSupervisorEvidence.model_validate(raw)


def test_new_and_original_schema_exports_match_runtime_without_rewriting_original():
    from pathlib import Path
    from ai_quant_os.supervisor_drift import TaskPlan
    from ai_quant_os.supervisor_agent import TaskPlan as OriginalTaskPlan

    root = Path(__file__).resolve().parents[1]
    assert (
        json.loads((root / "docs/schemas/task-plan-0.3.0.schema.json").read_text(encoding="utf-8"))
        == TaskPlan.model_json_schema()
    )
    assert (
        json.loads((root / "docs/schemas/task-plan.schema.json").read_text(encoding="utf-8"))
        == OriginalTaskPlan.model_json_schema()
    )


def test_failed_drift_child_is_immediately_reported_to_health_without_waiting_for_scan(
    tmp_path, monkeypatch
):
    import asyncio
    from ai_quant_os import drift_agent, health_watchdog

    async def malfunction(invocation):
        raise RuntimeError("private child failure")

    async def dormant(worker):
        await asyncio.Event().wait()

    monkeypatch.setattr(drift_agent, "assess_drift", malfunction)
    monkeypatch.setattr(health_watchdog, "run_health_watchdog", dormant)
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=True,
        )
    ) as client:
        _, plan = run(client)
        assert plan["calls"][-1]["status"] == "FAILED"
        incidents = client.get("/api/v1/paper/health/incidents", headers=HEADERS).json()[
            "incidents"
        ]
        assert any(
            row["agent_id"] == "A14" and row["code"] == "AGENT_FAILED" and row["quarantine"]
            for row in incidents
        )
        registry = client.get("/api/v1/agents/registry").json()["agents"]
        assert (
            next(
                row
                for row in registry
                if row["agent_id"] == "A14" and row["agent_version"] == "0.2.0"
            )["lifecycle"]
            == "QUARANTINED"
        )
