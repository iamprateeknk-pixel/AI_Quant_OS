from datetime import datetime, timezone, timedelta
import json
import pytest

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-supervisor"}


def execute(client, run_id="supervisor-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={"agent_id": "A01", "agent_version": "0.2.0", "task_id": "supervision"},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={"run_id": run_id, "context_ref": response.json()["context_ref"]},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return receipt


def test_supervisor_calls_six_actual_specialists_and_retains_receipts_after_restart(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        before = client.get("/api/v1/orders").json()
        receipt = execute(client)
        output = json.loads(receipt["output_json"])
        assert [row["agent_id"] for row in output["calls"]] == [
            "A07",
            "A06",
            "A05",
            "A02",
            "A04",
            "A03",
        ]
        assert {row["agent_id"]: row["status"] for row in output["calls"]} == {
            "A07": "COMPLETED",
            "A06": "COMPLETED",
            "A05": "COMPLETED",
            "A02": "WAIT_FOR_DATA",
            "A04": "COMPLETED",
            "A03": "COMPLETED",
        }
        assert "A02_WAIT_FOR_DATA" in output["contradictions"]
        agent_count = len(client.get("/api/v1/agents/registry", headers=HEADERS).json()["agents"])
        assert len(output["deferred"]) == agent_count - len(output["calls"]) - 1
        for version in ("0.3.0", "0.4.0"):
            assert any(
                row["agent_id"] == "A01"
                and row["agent_version"] == version
                and row["reason"] == "UNSUPPORTED"
                for row in output["deferred"]
            )
        assert any(
            row["agent_id"] == "A14"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )
        assert any(
            row["agent_id"] == "A10"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )
        assert any(
            row["agent_id"] == "A09"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )
        assert any(
            row["agent_id"] == "A08"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )
        assert any(
            row["agent_id"] == "A18"
            and row["agent_version"] == "0.2.0"
            and row["reason"] == "UNSUPPORTED"
            for row in output["deferred"]
        )
        assert output["confidence"] == "0" and output["requires_escalation"]
        assert output["health_status"] == "UNAVAILABLE"
        assert client.get("/api/v1/orders").json() == before
        for row in output["calls"]:
            child = client.get("/api/v1/agents/graph-runs/" + row["run_id"], headers=HEADERS)
            assert child.status_code == 200
            assert child.json()["receipt"]["context_ref"] == row["context_ref"]
            if row["status"] == "COMPLETED":
                assert (
                    json.loads(child.json()["receipt"]["output_json"])["agent_id"]
                    == row["agent_id"]
                )
            else:
                assert child.json()["receipt"]["output_json"] is None
        retry = client.post("/api/v1/paper/agents/graph-runs/supervisor-1/resume", headers=HEADERS)
        assert retry.json()["receipt"] == receipt
        another = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "supervisor-2", "context_ref": receipt["context_ref"]},
        )
        assert another.json()["receipt"]["status"] == "COMPLETED"
        assert json.loads(another.json()["receipt"]["output_json"])["calls"] == output["calls"]
    with TestClient(create_app(**args)) as client:
        archived = client.get("/api/v1/agents/graph-runs/supervisor-1", headers=HEADERS)
        assert archived.json()["receipt"] == receipt
        for row in output["calls"]:
            assert (
                client.get(
                    "/api/v1/agents/graph-runs/" + row["run_id"], headers=HEADERS
                ).status_code
                == 200
            )


def test_supervisor_manifest_is_partial_and_original_is_still_planned(tmp_path):
    with TestClient(create_app(paper_db_path=tmp_path / "paper.sqlite3")) as client:
        agents = client.get("/api/v1/agents/registry").json()["agents"]
        assert len(agents) == 40
        rows = {row["agent_version"]: row for row in agents if row["agent_id"] == "A01"}
        assert rows["0.1.0"]["implementation_status"] == "PLANNED"
        assert rows["0.2.0"]["implementation_status"] == "PARTIAL"
        assert rows["0.2.0"]["lifecycle"] == "SHADOW"
        assert rows["0.3.0"]["implementation_status"] == "PARTIAL"
        assert rows["0.3.0"]["lifecycle"] == "SHADOW"
        assert rows["0.3.0"]["allowed_tools"] == rows["0.2.0"]["allowed_tools"]
        assert rows["0.4.0"]["implementation_status"] == "PARTIAL"
        assert rows["0.4.0"]["lifecycle"] == "SHADOW"
        assert rows["0.4.0"]["allowed_tools"] == rows["0.3.0"]["allowed_tools"]
        assert rows["0.4.0"]["required_inputs"] == rows["0.3.0"]["required_inputs"] + ["task_spec"]
        assert set(rows["0.2.0"]["allowed_tools"]) == {
            "WORLD_STATE_READ",
            "HEALTH_READ",
            "TASK_ORCHESTRATE",
        }


def test_preview_cannot_wake_agents_without_operator_access(tmp_path):
    with TestClient(create_app(paper_db_path=tmp_path / "paper.sqlite3")) as client:
        response = client.post(
            "/api/v1/paper/agents/context",
            json={"agent_id": "A01", "agent_version": "0.2.0", "task_id": "preview"},
        )
        assert response.status_code == 503


@pytest.mark.parametrize("kind", ["stale", "spread"])
def test_supervision_preserves_actual_hard_order_blocks(tmp_path, kind):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        response = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1",
                "ask": "1.1002",
                "observed_at": (
                    NOW - timedelta(seconds=10) if kind == "stale" else NOW
                ).isoformat(),
                "world_state_version": "supervisor-market",
            },
        )
        assert response.status_code == 200

        def order(identifier):
            response = client.post(
                "/api/v1/paper/intents",
                headers=HEADERS | {"Idempotency-Key": identifier},
                json={
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
                    "world_state_version": "supervisor-market",
                    "evidence_refs": ["fixture:supervisor"],
                    "created_at": NOW.isoformat(),
                },
            )
            assert response.status_code == 200
            return response.json()

        before = order("before-supervisor")
        execute(client)
        after = order("after-supervisor")
        assert before["status"] == after["status"] == "REJECT"
        assert before["order_id"] is after["order_id"] is None
        violation = "STALE_MARKET" if kind == "stale" else "SPREAD_LIMIT"
        events = client.get("/api/v1/audit").json()["events"]
        for identifier in ("before-supervisor", "after-supervisor"):
            assert any(
                row["intent_id"] == identifier and violation in row["reason"] for row in events
            )
