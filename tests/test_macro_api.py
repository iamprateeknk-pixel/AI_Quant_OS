from datetime import timedelta
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.rate_archive import RateArchive
from test_policy_rates import NOW, recipe

HEADERS = {"x-operator-token": "fixture-macro"}


def execute(client, run_id="macro-api-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A03", "agent_version": "0.2.0", "task_id": "macro-api"},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    response = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": run_id, "context_ref": context["context_ref"]},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return receipt


def test_api_reads_checked_rate_archive_and_retains_exact_receipt_after_restart(tmp_path):
    with RateArchive(tmp_path / "rates.sqlite3") as archive:
        capture = archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    args = dict(
        operator_token="fixture-macro", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
    )
    with TestClient(create_app(**args)) as client:
        receipt = execute(client)
        output = json.loads(receipt["output_json"])
        assert output["rate_capture_ref"] == capture.capture_ref and len(output["drivers"]) == 3
        assert all(row["bias"] == "UNASSESSED" for row in output["currencies"])
    with TestClient(create_app(**args)) as client:
        response = client.get("/api/v1/agents/graph-runs/macro-api-1", headers=HEADERS)
        assert (
            response.status_code == 200
            and response.json()["receipt"]["output_json"] == receipt["output_json"]
        )


def test_no_rate_archive_yields_explicit_missing_fact_without_creating_database(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-macro",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        output = json.loads(execute(client)["output_json"])
        assert output["rate_receipt_status"] == "MISSING" and output["drivers"] == []
    assert not (tmp_path / "rates.sqlite3").exists()


def test_corrupt_rate_archive_refuses_context_without_healing(tmp_path):
    path = tmp_path / "rates.sqlite3"
    with RateArchive(path) as archive:
        archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE rate_captures SET raw_csv=?", (b"changed",))
    with TestClient(
        create_app(
            operator_token="fixture-macro",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        response = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A03", "agent_version": "0.2.0", "task_id": "corrupt"},
        )
        assert response.status_code == 503
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT raw_csv FROM rate_captures").fetchone()[0] == b"changed"


def test_locked_rate_archive_returns_unavailable_and_preserves_original_capture(tmp_path):
    path = tmp_path / "rates.sqlite3"
    with RateArchive(path) as archive:
        capture = archive.publish(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    with TestClient(
        create_app(
            operator_token="fixture-macro",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        connection = sqlite3.connect(path)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            response = client.post(
                "/api/v1/paper/agents/context",
                headers=HEADERS,
                json={"agent_id": "A03", "agent_version": "0.2.0", "task_id": "locked"},
            )
            assert response.status_code == 503
        finally:
            connection.rollback()
            connection.close()
        output = json.loads(execute(client)["output_json"])
        assert output["rate_capture_ref"] == capture.capture_ref


@pytest.mark.parametrize("kind", ["stale", "spread"])
def test_actual_order_gateway_keeps_its_rejection_after_macro_assessment(tmp_path, kind):
    with TestClient(
        create_app(
            operator_token="fixture-macro",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        quote = {
            "instrument_id": "EURUSD",
            "bid": "1.1",
            "ask": "1.1002",
            "observed_at": (NOW - timedelta(seconds=10) if kind == "stale" else NOW).isoformat(),
            "world_state_version": "macro-market",
        }
        assert client.post("/api/v1/paper/quotes", headers=HEADERS, json=quote).status_code == 200

        def order(identifier):
            payload = {
                "intent_id": identifier,
                "strategy_id": "demo",
                "strategy_version": "1",
                "instrument_id": "EURUSD",
                "side": "BUY",
                "desired_exposure": "1000",
                "risk_budget": "100",
                "max_spread": "0.00001" if kind == "spread" else "0.0003",
                "max_slippage": "0.0002",
                "valid_until": (NOW + timedelta(seconds=60)).isoformat(),
                "world_state_version": "macro-market",
                "evidence_refs": ["fixture:macro"],
                "created_at": NOW.isoformat(),
            }
            response = client.post(
                "/api/v1/paper/intents",
                headers=HEADERS | {"Idempotency-Key": identifier},
                json=payload,
            )
            assert response.status_code == 200
            return response.json()

        before = order("before-macro")
        execute(client)
        after = order("after-macro")
        assert before["status"] == after["status"] == "REJECT"
        assert before["order_id"] is after["order_id"] is None
        violation = "STALE_MARKET" if kind == "stale" else "SPREAD_LIMIT"
        events = client.get("/api/v1/audit").json()["events"]
        for identifier in ("before-macro", "after-macro"):
            assert any(
                row["intent_id"] == identifier and violation in row["reason"] for row in events
            )
