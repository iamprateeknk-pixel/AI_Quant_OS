import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.graph_runtime import AgentImplementation
from ai_quant_os.market_data import Bar

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture"}


def history(closes):
    return tuple(
        Bar(
            "EURUSD",
            NOW - timedelta(seconds=len(closes) - i - 1),
            Decimal(str(close)),
            Decimal(str(close)),
            Decimal(str(close)),
            Decimal(str(close)),
            Decimal(1),
        )
        for i, close in enumerate(closes)
    )


def test_multi_horizon_features_and_insufficient_windows_are_explicit():
    assert importlib.util.find_spec("ai_quant_os.regime_features") is not None
    from ai_quant_os.regime_features import regime_features

    rows = regime_features(history([Decimal(1) + Decimal("0.001") * i for i in range(64)]))
    assert [row.horizon for row in rows] == ["SHORT", "MEDIUM", "LONG"]
    assert [row.bar_count for row in rows] == [8, 24, 64]
    assert all(row.status == "AVAILABLE" and row.efficiency == 1 for row in rows)
    assert rows[0].observed_span_seconds == 7
    short = regime_features(history([1, 1.001, 1.002]))
    assert short[0].status == "AVAILABLE"
    assert [row.status for row in short[1:]] == ["INSUFFICIENT", "INSUFFICIENT"]
    assert all(row.return_bps is None for row in short[1:])


def execute(client, run_id="regime-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={
            "agent_id": "A02",
            "agent_version": "0.2.0",
            "task_id": "regime-task",
        },
    )
    assert response.status_code == 200, response.text
    context = response.json()
    result = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": run_id, "context_ref": context["context_ref"]},
    )
    assert result.status_code == 200, result.text
    return context, result.json()["receipt"]


def publish(client, closes):
    response = client.post(
        "/api/v1/paper/world-state/bars",
        headers=HEADERS,
        json={
            "bars": [
                {
                    "instrument_id": bar.instrument_id,
                    "observed_at": bar.observed_at.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                }
                for bar in history(closes)
            ]
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/v1/paper/quotes",
        headers=HEADERS,
        json={
            "instrument_id": "EURUSD",
            "bid": str(closes[-1]),
            "ask": str(Decimal(str(closes[-1])) + Decimal("0.0001")),
            "observed_at": NOW.isoformat(),
            "world_state_version": "fixture",
        },
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("trend", "TRENDING"),
        ("range", "RANGING"),
        ("volatile", "HIGH_VOLATILITY"),
    ],
)
def test_real_agent_executes_through_graph_with_versioned_envelope(tmp_path, kind, expected):
    closes = (
        [Decimal(1) + Decimal("0.001") * i for i in range(64)]
        if kind == "trend"
        else [Decimal(1)] * 64
        if kind == "range"
        else [Decimal(1) if i % 2 else Decimal("1.01") for i in range(64)]
    )
    path = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(operator_token="fixture", clock=lambda: NOW, paper_db_path=path)
    ) as client:
        publish(client, closes)
        context, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["schema_version"] == "2.0"
        assert output["agent_id"] == "A02" and output["agent_version"] == "0.2.0"
        assert output["task_id"] == "regime-task"
        assert output["input_refs"] == context["input_refs"]
        assert output["valid_until"] == context["valid_until"]
        assert output["requires_escalation"] is True
        assert Decimal(output["confidence"]) == 0 and Decimal(output["uncertainty"]) == 1
        assert output["trace_id"]
        eur = next(row for row in output["assessments"] if row["instrument_id"] == "EURUSD")
        assert all(row["regime"] == expected for row in eur["horizons"])
        assert eur["event_status"] == eur["crisis_status"] == "UNAVAILABLE"
        assert all(
            sum(Decimal(x) for x in row["distribution"].values()) == 1 for row in eur["horizons"]
        )
        missing = next(row for row in output["assessments"] if row["instrument_id"] == "GBPUSD")
        assert all(row["regime"] == "UNKNOWN" for row in missing["horizons"])
        assert "CALIBRATION_UNAVAILABLE" in output["contradictions"]
    with TestClient(
        create_app(operator_token="fixture", clock=lambda: NOW, paper_db_path=path)
    ) as client:
        archived = client.get("/api/v1/agents/graph-runs/regime-1", headers=HEADERS)
        assert archived.json()["receipt"] == receipt


def test_agent_failure_is_contained_while_paper_core_remains_available(tmp_path):
    app = create_app(
        operator_token="fixture",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(app) as client:
        runtime = app.state.graph_runtime
        key = ("A02", "0.2.0")
        installed = runtime._implementations[key]

        async def failed_handler(_invocation):
            raise RuntimeError("private agent failure")

        runtime._implementations[key] = AgentImplementation(
            installed.manifest_ref, installed.output_schema, failed_handler
        )
        publish(client, [Decimal(1) + Decimal("0.001") * i for i in range(64)])
        _, receipt = execute(client, "failed-agent-run")
        assert receipt["status"] == "FAILED"
        assert receipt["output_json"] is None
        assert "private agent failure" not in json.dumps(receipt)
        assert client.get("/api/v1/system/status").status_code == 200
        assert client.get("/api/v1/orders").status_code == 200
        assert client.get("/api/v1/risk").json()["safety_state"] == "NORMAL"


def test_horizon_disagreement_is_reported(tmp_path):
    closes = [Decimal(1) + Decimal("0.001") * i for i in range(56)] + [Decimal("1.055")] * 8
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        publish(client, closes)
        _, receipt = execute(client)
        output = json.loads(receipt["output_json"])
        assert "EURUSD:HORIZON_DISAGREEMENT" in output["contradictions"]


def test_agent_with_missing_market_never_invents_distribution(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        _, receipt = execute(client)
        assert receipt["status"] == "WAIT_FOR_DATA"
        assert receipt["output_json"] is None


def test_mismatched_history_is_not_bound_to_current_world():
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.paper_context_sources import paper_context_sources
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine

    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    engine.record_bars(history([1, 1.001, 1.002]), now=NOW)
    snapshot = engine.snapshot(
        (MarketSnapshot("EURUSD", Decimal("1.002"), Decimal("1.0021"), NOW, "fixture", True),),
        now=NOW,
    )
    with pytest.raises(ValueError, match="does not match"):
        paper_context_sources(snapshot, {"EURUSD": history([1, 1.001, 1.003])})


def test_insufficient_horizons_do_not_copy_short_window_results(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        publish(client, [Decimal(1), Decimal("1.01"), Decimal("1.02")])
        _, receipt = execute(client)
        output = json.loads(receipt["output_json"])
        rows = output["assessments"][0]["horizons"]
        assert rows[0]["status"] == "AVAILABLE"
        assert all(
            row["regime"] == "UNKNOWN" and row["status"] == "INSUFFICIENT" for row in rows[1:]
        )
        assert "EURUSD:HISTORY_INSUFFICIENT" in output["contradictions"]


def test_runtime_rejects_typed_mission_output_with_wrong_task(tmp_path, monkeypatch):
    from ai_quant_os.graph_runtime import AgentImplementation
    from ai_quant_os.regime_agent import regime_implementation

    def install(registry):
        original = regime_implementation(registry)

        async def wrong_task(invocation):
            result = await original.invoke(invocation)
            return result.model_copy(update={"task_id": "forged-task"})

        return AgentImplementation(original.manifest_ref, original.output_schema, wrong_task)

    monkeypatch.setattr("ai_quant_os.api.regime_implementation", install)
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        publish(client, [Decimal(1), Decimal("1.01"), Decimal("1.02")])
        _, receipt = execute(client)
        assert receipt["status"] == "INVALID"
        assert receipt["output_json"] is None


def test_replay_evaluation_records_fixture_provenance_and_real_agent_results(tmp_path):
    import asyncio
    from pathlib import Path

    assert importlib.util.find_spec("ai_quant_os.regime_evaluation") is not None
    from ai_quant_os.regime_evaluation import evaluate_regime_fixture

    report = asyncio.run(
        evaluate_regime_fixture(Path(__file__).parent / "fixtures" / "regime_baseline.json")
    )
    assert report["agent_version"] == "0.2.0"
    assert report["case_count"] == 5
    assert report["correct_horizons"] == report["horizon_count"] == 15
    assert report["calibration_status"] == "UNAVAILABLE"
    assert report["promotion_approved"] is False
    assert len(report["fixture_sha256"]) == 64
    assert all(row["status"] == "COMPLETED" and row["elapsed_ms"] >= 0 for row in report["results"])
