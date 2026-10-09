import json
from datetime import timedelta
from decimal import Decimal
import pytest

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_execution_drift import NOW, module, sources


@pytest.mark.parametrize("kind,status", [("failed", "FAILED"), ("timeout", "TIMEOUT")])
def test_mission_failure_has_typed_receipt_and_retained_health_event(
    tmp_path, monkeypatch, kind, status
):
    import asyncio
    from ai_quant_os import drift_agent

    async def malfunction(invocation):
        if kind == "timeout":
            await asyncio.sleep(3)
        raise RuntimeError("private drift fault")

    monkeypatch.setattr(drift_agent, "assess_drift", malfunction)
    headers = {"X-Operator-Token": "operator"}
    with TestClient(
        create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            clock=lambda: NOW,
            health_watchdog_enabled=True,
        )
    ) as client:
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=headers,
            json={"agent_id": "A14", "agent_version": "0.2.0", "task_id": "malfunction"},
        ).json()
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=headers,
            json={"run_id": "malfunction", "context_ref": context["context_ref"]},
        )
        receipt = result.json()["receipt"]
        assert receipt["status"] == status and receipt["output_json"] is None
        assert "private drift fault" not in json.dumps(receipt)
        incidents = client.get("/api/v1/paper/health/incidents", headers=headers).json()[
            "incidents"
        ]
        assert any(
            row["agent_id"] == "A14" and row["code"] == "AGENT_" + status and row["quarantine"]
            for row in incidents
        )
        assert client.get("/api/v1/orders").json()["orders"] == []


def test_actual_a14_graph_reports_missing_live_shadow_and_prediction_evidence(tmp_path):
    settings = dict(
        operator_token="operator",
        paper_db_path=tmp_path / "paper.sqlite3",
        clock=lambda: NOW,
        drift_policy=module().DriftPolicy(3600, 2, Decimal(10)),
    )
    with TestClient(create_app(**settings)) as client:
        payload = {"agent_id": "A14", "agent_version": "0.2.0", "task_id": "drift-empty"}
        assert client.post("/api/v1/paper/agents/context", json=payload).status_code == 401
        headers = {"X-Operator-Token": "operator"}
        response = client.post("/api/v1/paper/agents/context", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        context = response.json()
        response = client.post(
            "/api/v1/paper/agents/execute",
            headers=headers,
            json={"run_id": "drift-empty", "context_ref": context["context_ref"]},
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_id"] == "A14" and output["agent_version"] == "0.2.0"
        assert output["report"]["assessments"] == []
        assert set(output["contradictions"]) >= {
            "LIVE_RESULTS_UNAVAILABLE",
            "SHADOW_RESULTS_UNAVAILABLE",
            "PREDICTIONS_UNAVAILABLE",
            "CALIBRATION_UNAVAILABLE",
        }
        assert output["confidence"] == "0" and output["uncertainty"] == "1"
        assert output["demotion_recommendation"] == "UNASSESSED"
        assert client.get("/api/v1/orders").json()["orders"] == []
    with TestClient(create_app(**settings)) as client:
        response = client.post(
            "/api/v1/paper/agents/graph-runs/drift-empty/resume", headers=headers
        )
        assert response.json()["receipt"] == receipt


def test_mission_source_comparison_is_exactly_bound_to_task_and_original_cost_evidence(tmp_path):
    import asyncio
    import importlib
    from ai_quant_os.agent_context import ContextCompiler
    from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
    from ai_quant_os.graph_runtime import GraphRuntime

    source_module = importlib.import_module("ai_quant_os.drift_sources")
    agent = importlib.import_module("ai_quant_os.drift_agent")

    async def run():
        registry = load_agent_catalog()
        declared = module().DriftPolicy(3600, 2, Decimal(10))
        bindings = source_module.drift_sources(*sources(), now=NOW, policy=declared)
        context = await ContextCompiler(
            registry, bindings, clock=lambda: NOW, max_input_bytes=65536, max_context_bytes=100000
        ).compile("A14", "0.2.0", task_id="cost-change", scope="PAPER")
        async with GraphRuntime.open(
            tmp_path / "graph.sqlite3",
            registry=registry,
            implementations={("A14", "0.2.0"): agent.drift_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: NOW,
        ) as runtime:
            receipt = await runtime.run("cost-change", context)
            assert receipt.status == "COMPLETED", receipt
            output = agent.DriftAttribution.model_validate_json(receipt.output_json)
            assert output.report.assessments[0].state == "COST_INCREASE"
            assert output.report.assessments[0].cost_delta_bps == Decimal("19.998000")
            output.require_context(context)
            forged = output.model_copy(
                update={
                    "report": module().compare_execution_drift(
                        *sources(), now=NOW + timedelta(seconds=1), policy=declared
                    )
                }
            )
            import pytest

            with pytest.raises(ValueError, match="source"):
                forged.require_context(context)
            with pytest.raises(ValueError, match="source"):
                output.model_copy(update={"contradictions": ()}).require_context(context)

    asyncio.run(run())


def test_actual_retained_fill_is_observed_and_corrupt_benchmark_withholds_context(tmp_path):
    from test_execution_attribution import api_account
    from test_paper_attribution import NOW as LEDGER_NOW
    import sqlite3

    seed, headers = api_account(tmp_path)
    seed.close()
    with TestClient(
        create_app(
            operator_token="test-secret",
            paper_db_path=tmp_path / "paper.sqlite3",
            clock=lambda: LEDGER_NOW,
            drift_policy=module().DriftPolicy(3600, 2, Decimal(10)),
        )
    ) as client:
        payload = {"agent_id": "A14", "agent_version": "0.2.0", "task_id": "actual-ledger"}
        response = client.post("/api/v1/paper/agents/context", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        context = response.json()
        response = client.post(
            "/api/v1/paper/agents/execute",
            headers=headers,
            json={"run_id": "actual-ledger", "context_ref": context["context_ref"]},
        )
        receipt = response.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        row = json.loads(receipt["output_json"])["report"]["assessments"][0]
        assert row["current"]["fill_count"] == row["current"]["benchmark_fill_count"] == 1
        assert row["state"] == "UNASSESSED" and row["cost_delta_bps"] is None
        with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
            db.execute("UPDATE paper_price_benchmarks SET benchmark_json='{}'")
        response = client.post(
            "/api/v1/paper/agents/context",
            headers=headers,
            json={**payload, "task_id": "damaged-ledger"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Drift execution evidence unavailable"


def test_unconfigured_policy_and_quarantine_are_explicit_in_actual_runtime(tmp_path):
    app = create_app(
        operator_token="operator", paper_db_path=tmp_path / "paper.sqlite3", clock=lambda: NOW
    )
    headers = {"X-Operator-Token": "operator"}
    with TestClient(app) as client:
        payload = {"agent_id": "A14", "agent_version": "0.2.0", "task_id": "unconfigured"}
        context = client.post("/api/v1/paper/agents/context", headers=headers, json=payload).json()
        response = client.post(
            "/api/v1/paper/agents/execute",
            headers=headers,
            json={"run_id": "unconfigured", "context_ref": context["context_ref"]},
        )
        output = json.loads(response.json()["receipt"]["output_json"])
        assert output["report"]["state"] == "POLICY_UNCONFIGURED"
        app.state.graph_runtime._registry.set_lifecycle("A14", "0.2.0", "QUARANTINED")
        denied = client.post(
            "/api/v1/paper/agents/context",
            headers=headers,
            json={**payload, "task_id": "quarantined"},
        )
        assert denied.status_code == 403


def test_policy_file_requires_exact_bounded_fields_and_decimal_text(tmp_path, monkeypatch):
    from ai_quant_os.drift_sources import drift_configuration
    import pytest

    path = tmp_path / "policy.json"
    monkeypatch.setenv("AI_QUANT_DRIFT_POLICY", str(path))
    path.write_text(
        '{"window_seconds":3600,"minimum_fills":2,"adverse_cost_increase_bps":"10"}',
        encoding="utf-8",
    )
    assert drift_configuration(None) == module().DriftPolicy(3600, 2, Decimal(10))
    for raw in [
        '{"window_seconds":3600,"minimum_fills":2,"adverse_cost_increase_bps":10}',
        '{"window_seconds":3600,"window_seconds":60,"minimum_fills":2,"adverse_cost_increase_bps":"10"}',
        '{"window_seconds":3600,"minimum_fills":2,"adverse_cost_increase_bps":"invalid"}',
        "x" * 4097,
    ]:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(ValueError):
            drift_configuration(None)


def test_published_drift_output_schema_matches_runtime():
    from pathlib import Path
    from ai_quant_os.drift_agent import DriftAttribution

    saved = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/drift-attribution.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved == DriftAttribution.model_json_schema()
