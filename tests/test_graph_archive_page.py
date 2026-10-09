from fastapi.testclient import TestClient
import json
import sqlite3
from pathlib import Path

import pytest

from ai_quant_os.api import create_app
from test_supervisor_api import NOW, HEADERS
from test_task_supervisor import task_run


def test_saved_execution_page_is_protected_bounded_and_links_original_traces(tmp_path):
    args = dict(
        operator_token="fixture-supervisor",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**args)) as client:
        url = "/api/v1/agents/graph-runs"
        assert client.get(url).status_code == 401
        response = client.get(url, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["records"] == []
        task_run(client, "DATA_REVIEW")
        first = client.get(url + "?limit=1", headers=HEADERS).json()
        assert first["total"] == 2 and len(first["records"]) == 1
        assert first["next_after"] == first["records"][-1]["run_id"]
        second = client.get(
            url,
            params={"limit": 1, "after_run_id": first["next_after"], "head": first["head_hash"]},
            headers=HEADERS,
        ).json()
        assert second["next_after"] is None
        records = first["records"] + second["records"]
        assert len({row["run_id"] for row in records}) == 2
        for row in records:
            trace = client.get(url + "/" + row["run_id"] + "/trace", headers=HEADERS).json()
            assert trace["trace_id"] == row["trace_id"]
            assert trace["status"] == row["status"]
            assert "output_json" not in row and "checkpoint" not in row
        assert (
            client.get(
                url, params={"after_run_id": first["next_after"]}, headers=HEADERS
            ).status_code
            == 422
        )
        task_run(client, "EVENT_REVIEW", "new-task")
        assert (
            client.get(
                url,
                params={"after_run_id": first["next_after"], "head": first["head_hash"]},
                headers=HEADERS,
            ).status_code
            == 409
        )
    with TestClient(create_app(**args)) as client:
        assert client.get(url, headers=HEADERS).json()["total"] == 4


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 26},
        {"after_run_id": "../orders", "head": "a" * 64},
        {"head": "bad"},
        {"after_run_id": "run_1"},
    ],
)
def test_invalid_page_never_consumes_role_quota(tmp_path, params):
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
            client.get("/api/v1/agents/graph-runs", params=params, headers=HEADERS).status_code
            == 422
        )
        assert all(
            row["daily_calls"] == 0
            for row in client.get("/api/v1/agents/call-budget", headers=HEADERS).json()["agents"]
        )


@pytest.mark.parametrize("corruption", ["journal", "deleted_projection", "projection_hash"])
def test_archive_page_withholds_corrupt_records_without_mutation(tmp_path, corruption):
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        task_run(client, "DATA_REVIEW")
        with sqlite3.connect(tmp_path / "paper.agents.sqlite3") as db:
            if corruption == "journal":
                db.execute(
                    "UPDATE audit_events SET event_json='{}' WHERE event_key='graph:task-review:CREATED'"
                )
            elif corruption == "deleted_projection":
                db.execute("DELETE FROM graph_runs WHERE run_id='task-review'")
            else:
                db.execute(
                    "UPDATE graph_runs SET record_hash=? WHERE run_id='task-review'", ("0" * 64,)
                )
        assert client.get("/api/v1/agents/graph-runs", headers=HEADERS).status_code == 503
        assert client.get("/api/v1/orders").status_code == 200


def test_page_read_is_archival_read_only_and_has_no_quota_attempts(tmp_path):
    from ai_quant_os.graph_ledger import GraphLedger
    from ai_quant_os.agent_call_budgets import AgentCallPolicy, AgentCallRule

    policy = AgentCallPolicy(tuple(AgentCallRule(f"A{i:02}", 20, 100, 0, 0) for i in range(1, 19)))
    path = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(
            operator_token="fixture-supervisor",
            clock=lambda: NOW,
            paper_db_path=path,
            agent_call_policy=policy,
        )
    ) as client:
        task_run(client, "DATA_REVIEW")
        before = client.get("/api/v1/agents/call-budget", headers=HEADERS).json()
        first = client.get("/api/v1/agents/graph-runs", headers=HEADERS).json()
        assert client.get("/api/v1/agents/graph-runs", headers=HEADERS).json() == first
        assert client.get("/api/v1/agents/call-budget", headers=HEADERS).json() == before
        assert (
            client.get(
                "/api/v1/agents/graph-runs",
                params={"after_run_id": "missing", "head": first["head_hash"]},
                headers=HEADERS,
            ).status_code
            == 404
        )
    with GraphLedger.archive(tmp_path / "paper.agents.sqlite3") as archive:
        assert archive.page().model_dump(mode="json") == first
        with pytest.raises(sqlite3.Error):
            archive._db.execute("DELETE FROM graph_runs")


def test_page_schema_and_count_identity_claims():
    from ai_quant_os.graph_archive import GraphRunPage

    schema = Path(__file__).resolve().parents[1] / "docs/schemas/graph-run-page.schema.json"
    assert json.loads(schema.read_text(encoding="utf-8")) == GraphRunPage.model_json_schema()
    base = dict(
        head_hash="0" * 64, total=0, after_run_id="", remaining=0, next_after=None, records=[]
    )
    GraphRunPage(**base)
    for fields in [dict(total=1), dict(remaining=1), dict(next_after="made-up"), dict(total=True)]:
        with pytest.raises(ValueError):
            GraphRunPage(**(base | fields))
