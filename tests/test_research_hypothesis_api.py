from datetime import datetime, timezone
from decimal import Decimal
import json
import asyncio

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-hypothesis"}


def test_bounded_research_batches_retain_all_anomalies_and_defer_remaining_drafts(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        for index in range(6):
            csv = training_csv().replace("2026-10-01", f"2026-09-{index + 1:02}")
            published = client.post(
                "/api/v1/paper/research/datasets",
                headers=HEADERS,
                json={"source_id": f"history-{index}", "resource_class": "TRAINING", "csv": csv},
            )
            assert published.status_code == 200
        _, _, first = run(client, "batch-first")
        assert first["training_dataset_count"] == first["eligible_anomaly_count"] == 6
        assert len(first["hypotheses"]) == 4 and first["deferred_hypothesis_count"] == 2
        _, _, second = run(client, "batch-second")
        assert second["already_proposed_count"] == 4 and len(second["hypotheses"]) == 2
        assert second["deferred_hypothesis_count"] == 0
        assert {row["dataset_fingerprint"] for row in first["hypotheses"]}.isdisjoint(
            row["dataset_fingerprint"] for row in second["hypotheses"]
        )


def test_active_novelty_reservation_suppresses_a_concurrent_research_context(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        seed(client)

        def compile_context(task_id, agent_version="0.3.0"):
            response = client.post(
                "/api/v1/sandbox/research/context",
                headers=HEADERS,
                json={"task_id": task_id, "agent_version": agent_version},
            )
            assert response.status_code == 200, response.text
            return response.json()

        first = compile_context("novelty-first", "0.2.0")
        second = compile_context("novelty-second")
        assert first["agent_version"] == "0.2.0" and second["agent_version"] == "0.3.0"
        assert first["status"] == "READY" and second["status"] == "READY"
        first_run = client.post(
            "/api/v1/sandbox/research/execute",
            headers=HEADERS,
            json={"run_id": "novelty-first", "context_ref": first["context_ref"]},
        )
        second_run = client.post(
            "/api/v1/sandbox/research/execute",
            headers=HEADERS,
            json={"run_id": "novelty-second", "context_ref": second["context_ref"]},
        )
        assert first_run.status_code == second_run.status_code == 200
        first_output = json.loads(first_run.json()["receipt"]["output_json"])
        second_output = json.loads(second_run.json()["receipt"]["output_json"])
        assert len(first_output["hypotheses"]) == 1
        assert second_output["hypotheses"] == []
        assert second_output["reserved_hypothesis_count"] == 1
        assert second_output["novelty_scope"] == "COMPLETED_AND_ACTIVE_RESERVATIONS_AT_CAPTURE"
        third = compile_context("novelty-third")
        third_run = client.post(
            "/api/v1/sandbox/research/execute",
            headers=HEADERS,
            json={"run_id": "novelty-third", "context_ref": third["context_ref"]},
        )
        assert third_run.status_code == 200
        third_output = json.loads(third_run.json()["receipt"]["output_json"])
        assert third_output["hypotheses"] == []
        assert third_output["already_proposed_count"] == 1
        assert third_output["reserved_hypothesis_count"] == 0


def test_v2_context_is_refused_when_an_a08_v3_context_holds_its_draft(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        seed(client)
        first = client.post(
            "/api/v1/sandbox/research/context",
            headers=HEADERS,
            json={"task_id": "lease-owner", "agent_version": "0.3.0"},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/v1/sandbox/research/context",
            headers=HEADERS,
            json={"task_id": "legacy-contender", "agent_version": "0.2.0"},
        )
        assert second.status_code == 503


def test_research_routes_require_operator_identity_and_reject_invented_authority(tmp_path):
    with TestClient(create_app()) as client:
        assert (
            client.post("/api/v1/sandbox/research/context", json={"task_id": "preview"}).status_code
            == 503
        )
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert (
            client.post(
                "/api/v1/sandbox/research/context", json={"task_id": "missing-token"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/sandbox/research/context",
                headers=HEADERS,
                json={"task_id": "wrong-scope", "scope": "PAPER"},
            ).status_code
            == 422
        )


def test_expired_research_context_has_typed_graph_failure_without_draft(tmp_path):
    from datetime import timedelta

    clock = [NOW]
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: clock[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        seed(client)
        context = client.post(
            "/api/v1/sandbox/research/context", headers=HEADERS, json={"task_id": "expiry"}
        ).json()
        clock[0] = NOW + timedelta(seconds=5)
        result = client.post(
            "/api/v1/sandbox/research/execute",
            headers=HEADERS,
            json={"run_id": "expiry", "context_ref": context["context_ref"]},
        )
        assert result.status_code == 200
        assert result.json()["receipt"]["status"] == "EXPIRED"
        assert result.json()["receipt"]["output_json"] is None


def test_paper_resume_cannot_route_a_sandbox_research_graph(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        run(client)
        response = client.post("/api/v1/paper/agents/graph-runs/research-1/resume", headers=HEADERS)
        assert response.status_code == 403


def test_research_failure_is_observed_before_reply_when_monitoring_is_enabled(
    tmp_path, monkeypatch
):
    from ai_quant_os import research_hypothesis_agent

    async def invalid_output(invocation):
        raise ValueError("Declared invalid research result")

    monkeypatch.setattr(research_hypothesis_agent, "generate_hypotheses", invalid_output)
    app = create_app(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
        health_watchdog_enabled=True,
    )
    with TestClient(app) as client:

        async def stop_unrelated_periodic_polling():
            task = app.state.health_task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            app.state.health_task = None

        client.portal.call(stop_unrelated_periodic_polling)
        compiled = client.post(
            "/api/v1/sandbox/research/context", headers=HEADERS, json={"task_id": "invalid"}
        )
        assert compiled.status_code == 200
        response = client.post(
            "/api/v1/sandbox/research/execute",
            headers=HEADERS,
            json={"run_id": "invalid", "context_ref": compiled.json()["context_ref"]},
        )
        assert response.status_code == 200 and response.json()["receipt"]["status"] == "INVALID"
        agents = client.get("/api/v1/agents/registry").json()["agents"]
        assert (
            next(
                row for row in agents if (row["agent_id"], row["agent_version"]) == ("A08", "0.2.0")
            )["lifecycle"]
            == "QUARANTINED"
        )
        assert response.json()["context_current"] is False


def training_csv(kind="trend", minute=0):
    prices = [
        Decimal("1.1") + Decimal(i) / 10000
        if kind == "trend"
        else Decimal("1.1") + Decimal(i % 2) / 10000
        if kind == "alternating"
        else Decimal("1.1")
        for i in range(12)
    ]
    return (
        "instrument_id,observed_at,open,high,low,close,volume\n"
        + "\n".join(
            f"EURUSD,2026-10-01T00:{minute + i:02}:00Z,{price},{price},{price},{price},1"
            for i, price in enumerate(prices)
        )
        + "\n"
    )


def seed(client, kind="trend"):
    response = client.post(
        "/api/v1/paper/research/datasets",
        headers=HEADERS,
        json={
            "source_id": "synthetic-development",
            "resource_class": "TRAINING",
            "csv": training_csv(kind),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["receipt"]


def run(client, task="research-1"):
    response = client.post(
        "/api/v1/sandbox/research/context", headers=HEADERS, json={"task_id": task}
    )
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["scope"] == "SANDBOX" and context["status"] == "READY"
    response = client.post(
        "/api/v1/sandbox/research/execute",
        headers=HEADERS,
        json={"run_id": task, "context_ref": context["context_ref"]},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    return context, receipt, json.loads(receipt["output_json"])


def test_actual_research_graph_proposes_causal_test_and_remembers_it_across_restart(tmp_path):
    settings = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**settings)) as client:
        dataset = seed(client)
        before = client.get("/api/v1/system/status").json()
        context, receipt, output = run(client)
        assert output["status"] == "PROPOSED" and output["training_dataset_count"] == 1
        assert output["confidence"] == "0" and output["uncertainty"] == "1"
        assert not output["execution_authorized"] and output["requires_escalation"]
        assert len(output["hypotheses"]) == 1
        hypothesis = output["hypotheses"][0]
        assert hypothesis["dataset_ref"] == dataset["dataset_ref"]
        assert hypothesis["rule"] == "FOLLOW_PREVIOUS_CLOSE_DIRECTION"
        assert hypothesis["eligible_pairs"] == hypothesis["same_direction_pairs"] == 10
        assert hypothesis["execution_timing"] == "NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"
        assert hypothesis["expected_costs"]["status"] == "UNAVAILABLE"
        assert "SEPARATE_CHRONOLOGICAL_OUT_OF_SAMPLE_BARS" in hypothesis["required_data"]
        assert client.get("/api/v1/system/status").json() == before
        assert (
            client.post(
                "/api/v1/paper/agents/execute",
                headers=HEADERS,
                json={"run_id": "wrong-scope", "context_ref": context["context_ref"]},
            ).status_code
            == 403
        )
    with TestClient(create_app(**settings)) as client:
        archived = client.get("/api/v1/agents/graph-runs/research-1", headers=HEADERS).json()
        assert archived["receipt"] == receipt
        _, _, next_output = run(client, "research-2")
        assert next_output["status"] == "NO_NEW_HYPOTHESIS" and next_output["hypotheses"] == []
        assert next_output["already_proposed_count"] == 1


def test_alternating_observations_propose_reversal_with_actual_archival_spread_evidence(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        seed(client, "alternating")
        quote = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1",
                "ask": "1.1001",
                "observed_at": NOW.isoformat(),
                "world_state_version": "synthetic-history",
            },
        )
        assert quote.status_code == 200
        archived = client.post("/api/v1/paper/world-state/publish", headers=HEADERS, json={})
        assert archived.status_code == 200, archived.text
        _, _, output = run(client)
        draft = output["hypotheses"][0]
        assert draft["rule"] == "FADE_PREVIOUS_CLOSE_DIRECTION"
        costs = draft["expected_costs"]
        assert (
            costs["status"] == "HISTORICAL_SPREAD_ONLY" and costs["spread_observation_count"] == 1
        )
        assert Decimal(costs["minimum_recorded_spread_bps"]) > 0
        assert costs["commission_status"] == "UNAVAILABLE"


def test_flat_or_missing_development_observations_withhold_hypotheses(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, _, empty = run(client, "empty")
        assert empty["status"] == "WAIT_FOR_EVIDENCE" and empty["hypotheses"] == []
        seed(client, "flat")
        _, _, flat = run(client, "flat")
        assert flat["status"] == "WAIT_FOR_EVIDENCE" and flat["hypotheses"] == []


def test_paper_api_refuses_holdout_publication_and_research_context_stays_training_only(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        holdout = client.post(
            "/api/v1/paper/research/datasets",
            headers=HEADERS,
            json={
                "source_id": "heldout-secret-history",
                "resource_class": "HOLDOUT",
                "csv": training_csv(),
            },
        )
        assert holdout.status_code == 422
        context, _, output = run(client)
        serialized = json.dumps({"context": context, "output": output})
        assert "heldout-secret-history" not in serialized
        assert output["training_dataset_count"] == 0 and output["hypotheses"] == []
