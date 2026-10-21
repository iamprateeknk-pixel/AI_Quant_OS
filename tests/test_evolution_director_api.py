"""A17 exposes a read-only evolution readiness report through the paper graph."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a17"}


def test_a17_returns_no_tournament_actions_or_promotion_authority(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a17",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A17", "agent_version": "0.2.0", "task_id": "evolution-review"},
        )
        assert context.status_code == 200, context.text
        assert context.json()["status"] == "READY"
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "evolution-review", "context_ref": context.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["plan_created"] is False
        assert output["tournament_actions"] == []
        assert output["promotion_allowed"] is False
        assert client.get("/api/v1/system/status").json() == before


def test_a17_quality_context_includes_audited_graph_durations(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a17",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        closes = [Decimal(1) + Decimal("0.0001") * index for index in range(64)]
        bars = client.post(
            "/api/v1/paper/world-state/bars",
            headers=HEADERS,
            json={
                "bars": [
                    {
                        "instrument_id": "EURUSD",
                        "observed_at": (NOW - timedelta(seconds=63 - index)).isoformat(),
                        "open": str(close),
                        "high": str(close),
                        "low": str(close),
                        "close": str(close),
                        "volume": "1",
                    }
                    for index, close in enumerate(closes)
                ]
            },
        )
        assert bars.status_code == 200, bars.text
        quote = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "EURUSD",
                "bid": str(closes[-1]),
                "ask": str(closes[-1] + Decimal("0.0001")),
                "observed_at": NOW.isoformat(),
                "world_state_version": "fixture",
            },
        )
        assert quote.status_code == 200, quote.text
        specialist = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A02", "agent_version": "0.2.0", "task_id": "quality-run"},
        )
        assert specialist.status_code == 200, specialist.text
        executed = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "quality-run", "context_ref": specialist.json()["context_ref"]},
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["receipt"]["status"] == "COMPLETED"

        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A17", "agent_version": "0.2.0", "task_id": "quality-review"},
        )
        assert context.status_code == 200, context.text
        quality = json.loads(context.json()["inputs"][0]["payload_json"])
        assert quality["source_trace_count"] == 1
        assert quality["rows"][0]["timing_sample_count"] == 1
        assert float(quality["rows"][0]["mean_duration_seconds"]) >= 0
