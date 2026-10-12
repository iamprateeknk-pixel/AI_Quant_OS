"""A15's paper graph reads retained replay evidence and never starts a replay."""

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-a15"}


def test_a15_missing_replay_returns_wait_and_does_not_change_paper_state(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-a15",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            research_workspace=tmp_path / "research",
        )
    ) as client:
        before = client.get("/api/v1/system/status").json()
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={"agent_id": "A15", "agent_version": "0.2.0", "task_id": "missing-replay"},
        )
        assert context.status_code == 200, context.text
        assert context.json()["status"] == "READY"
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={"run_id": "a15-review", "context_ref": context.json()["context_ref"]},
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "WAIT_FOR_EVIDENCE"
        assert output["alternatives"] == []
        assert output["simulation_only"] is True
        assert output["promotion_allowed"] is False
        assert client.get("/api/v1/system/status").json() == before


def test_a15_reviews_a_retained_native_diagnostic_as_simulation_only(tmp_path):
    import pytest

    pytest.importorskip("nautilus_trader")
    from ai_quant_os.counterfactual_pipeline import run_counterfactual
    from ai_quant_os.counterfactual_store import CounterfactualStore
    from test_counterfactual import setup_replay

    inputs, strategies = setup_replay(tmp_path)
    workspace = tmp_path / "research"
    workspace.mkdir()
    store = CounterfactualStore(workspace / "counterfactual.sqlite3")
    report = run_counterfactual(inputs, strategies, store)
    assert report.outcome == "COMPLETED"
    archive_files = tuple(
        path
        for path in (
            store.path,
            store.path.with_name(store.path.name + "-wal"),
            store.path.with_name(store.path.name + "-shm"),
        )
        if path.exists()
    )
    archive_bytes = {path: path.read_bytes() for path in archive_files}
    with TestClient(
        create_app(
            operator_token="fixture-a15",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            research_workspace=workspace,
        )
    ) as client:
        context = client.post(
            "/api/v1/paper/agents/context",
            headers=HEADERS,
            json={
                "agent_id": "A15",
                "agent_version": "0.2.0",
                "task_id": inputs.plan.replay_id,
            },
        )
        assert context.status_code == 200, context.text
        before = client.get("/api/v1/system/status").json()
        result = client.post(
            "/api/v1/paper/agents/execute",
            headers=HEADERS,
            json={
                "run_id": "a15-retained-review",
                "context_ref": context.json()["context_ref"],
            },
        )
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["state"] == "DIAGNOSTIC_ONLY"
        assert len(output["alternatives"]) == len(inputs.plan.variants)
        assert all(
            row["interpretation"] == "SIMULATED_ON_ALREADY_SEEN_DATA"
            for row in output["alternatives"]
        )
        assert output["simulation_only"] is True
        assert output["promotion_allowed"] is False
        assert client.get("/api/v1/system/status").json() == before
        assert {path: path.read_bytes() for path in archive_files} == archive_bytes
