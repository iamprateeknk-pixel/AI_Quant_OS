"""A10 reads checked original results through task-bound sandbox graphs."""

from datetime import timedelta
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.holdout_validator_rpc import HoldoutContextSnapshot, HoldoutResponse
from ai_quant_os.validation_scientist_sources import ValidationContextSnapshot
from test_candidate_validation_store import failed_tests
from test_candidate_validation import observed_reports
from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon
from test_research_hypothesis_api import NOW, HEADERS


def settings(tmp_path, clock=lambda: NOW):
    return dict(
        operator_token="fixture-hypothesis", clock=clock, paper_db_path=tmp_path / "paper.sqlite3"
    )


def run(client, reference, task="validate"):
    response = client.post(
        "/api/v1/sandbox/validation-scientist/context",
        headers=HEADERS,
        json={"task_id": task, "artifact_ref": reference},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    response = client.post(
        "/api/v1/sandbox/validation-scientist/execute",
        headers=HEADERS,
        json={"run_id": task, "context_ref": context["context_ref"]},
    )
    assert response.status_code == 200, response.text
    return context, response.json()["receipt"]


def test_failed_candidate_completes_advisory_agent_graph_without_trading_metrics(package, tmp_path):
    with CandidateValidationStore(tmp_path / "paper.candidate-validations.sqlite3") as archive:
        failure = archive.put_failure(package, failed_tests(package))
    with TestClient(create_app(**settings(tmp_path))) as client:
        before = client.get("/api/v1/system/status").json()
        context, receipt = run(client, failure.failure_ref)
        assert receipt["status"] == "COMPLETED", receipt
        output = json.loads(receipt["output_json"])
        assert output["agent_id"] == "A10" and output["agent_version"] == "0.2.0"
        assert output["summary"]["outcome"] == "PREREQUISITE_FAILED"
        assert output["summary"]["statistics"] is None
        assert output["summary"]["gates"][0]["state"] == "FAIL"
        assert all(row["state"] == "PENDING" for row in output["summary"]["gates"][1:])
        assert output["promotion_allowed"] is False and output["isolation_verified"] is False
        assert output["requires_escalation"] is True
        assert all(row["resource_class"] == "HOLDOUT" for row in context["inputs"])
        assert client.get("/api/v1/system/status").json() == before
        assert (
            client.post(
                "/api/v1/paper/agents/execute",
                headers=HEADERS,
                json={"run_id": "wrong-scope", "context_ref": context["context_ref"]},
            ).status_code
            == 403
        )
        assert client.get(
            "/api/v1/sandbox/validation-scientist/results/" + failure.failure_ref
        ).status_code in (401, 403)
    with TestClient(create_app(**settings(tmp_path))) as client:
        saved = client.get("/api/v1/agents/graph-runs/validate", headers=HEADERS)
        assert saved.json()["receipt"] == receipt
        resumed = client.post(
            "/api/v1/sandbox/validation-scientist/graph-runs/validate/resume", headers=HEADERS
        )
        assert resumed.status_code == 200 and resumed.json()["receipt"] == receipt


def test_complete_original_scorecard_is_summarized_with_every_gate(package, scripted, tmp_path):
    tested, holdout, windows = observed_reports(package, scripted)
    with CandidateValidationStore(tmp_path / "paper.candidate-validations.sqlite3") as archive:
        score = archive.put_evaluation(package, tested, holdout, windows)
    with TestClient(create_app(**settings(tmp_path))) as client:
        _, receipt = run(client, score.scorecard_ref)
        assert receipt["status"] == "COMPLETED", receipt
        summary = json.loads(receipt["output_json"])["summary"]
        assert summary["artifact_ref"] == score.scorecard_ref
        assert summary["statistics"]["holdout_net_pnl"] == str(score.holdout_net_pnl)
        assert tuple(
            (row["name"], row["state"], row["reason"]) for row in summary["gates"]
        ) == tuple((row.name, row.state, row.reason) for row in score.gates)
        assert summary["training_ref"] == package.training.receipt.dataset_ref
        assert summary["holdout_ref"] == package.holdout.receipt.dataset_ref
        from ai_quant_os.validation_scientist_contracts import ValidationSummary

        substituted = {
            **summary,
            "outcome": "VALIDATION_INCOMPLETE" if summary["outcome"] == "REJECTED" else "REJECTED",
        }
        with pytest.raises(ValueError):
            ValidationSummary.model_validate(substituted)


def test_expired_context_and_changed_archive_withhold_agent_output(package, tmp_path):
    now = [NOW]
    path = tmp_path / "paper.candidate-validations.sqlite3"
    with CandidateValidationStore(path) as archive:
        failure = archive.put_failure(package, failed_tests(package))
    with TestClient(create_app(**settings(tmp_path, clock=lambda: now[0]))) as client:
        context, _ = run(client, failure.failure_ref)
        now[0] += timedelta(seconds=6)
        response = client.post(
            "/api/v1/sandbox/validation-scientist/execute",
            headers=HEADERS,
            json={"run_id": "expired", "context_ref": context["context_ref"]},
        )
        assert response.status_code == 200
        assert response.json()["receipt"]["status"] == "EXPIRED"
        assert response.json()["receipt"]["output_json"] is None
        now[0] = NOW
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE candidate_validations SET artifact_json='{}'")
        response = client.post(
            "/api/v1/sandbox/validation-scientist/execute",
            headers=HEADERS,
            json={"run_id": "tampered", "context_ref": context["context_ref"]},
        )
        assert response.status_code == 200
        assert response.json()["receipt"]["status"] == "INVALID"
        assert response.json()["receipt"]["output_json"] is None


def test_a10_context_is_compiled_from_service_snapshot_without_local_archive_reads(
    tmp_path, monkeypatch
):
    from hashlib import sha256

    from ai_quant_os.candidate_validation_contracts import GATE_NAMES
    from ai_quant_os.research_data_contracts import ResearchDataReceipt, research_dataset_ref
    from ai_quant_os.validation_package_store import ValidationPackageStore
    from ai_quant_os.validation_scientist_contracts import (
        ValidationGateSummary,
        ValidationSummary,
    )

    raw_sha256 = sha256(b"service snapshot holdout").hexdigest()
    receipt = ResearchDataReceipt(
        dataset_ref=research_dataset_ref("service-test", raw_sha256, "HOLDOUT"),
        source_id="service-test",
        resource_class="HOLDOUT",
        raw_sha256=raw_sha256,
        dataset_fingerprint="1" * 64,
        instrument_id="EURUSD",
        row_count=2,
        first_observed_at=NOW - timedelta(minutes=2),
        last_observed_at=NOW - timedelta(minutes=1),
        received_at=NOW - timedelta(seconds=30),
    )
    artifact_ref = "candidate-prerequisite-failure:" + "2" * 64
    summary = ValidationSummary(
        artifact_ref=artifact_ref,
        package_ref="validation-package:" + "3" * 64,
        candidate_ref="strategy-candidate:" + "4" * 64,
        training_ref="research-data:" + "5" * 64,
        holdout_ref=receipt.dataset_ref,
        outcome="PREREQUISITE_FAILED",
        gates=tuple(
            ValidationGateSummary(
                name=name,
                state="FAIL" if index == 0 else "PENDING",
                reason="PREREQUISITE_NOT_SATISFIED",
            )
            for index, name in enumerate(GATE_NAMES)
        ),
        reasons=("PREREQUISITE_NOT_SATISFIED",),
        statistics=None,
    )
    snapshot = ValidationContextSnapshot(
        summary=summary,
        holdout_receipt=receipt,
        captured_at=NOW,
    )

    class Client:
        requests = []

        def call(self, request):
            self.requests.append(request)
            return HoldoutResponse(
                request_id=request.request_id,
                task_id=request.task_id,
                context_ref=request.context_ref,
                status="CHECKED_ARCHIVED_VALIDATION_CONTEXT",
                evidence_ref=request.evidence_ref,
                summary_sha256=summary.summary_sha256,
                aggregate={"outcome": summary.outcome, "gate_count": len(summary.gates)},
                context_snapshot=HoldoutContextSnapshot(
                    agent_id="A10", payload_json=snapshot.model_dump_json()
                ),
            )

    client = Client()

    def forbidden_archive_read(self, _reference):
        raise AssertionError("API context compilation read a local validation archive")

    monkeypatch.setattr(CandidateValidationStore, "get", forbidden_archive_read)
    monkeypatch.setattr(ValidationPackageStore, "get", forbidden_archive_read)
    with TestClient(create_app(**settings(tmp_path), holdout_rpc_client=client)) as api:
        response = api.post(
            "/api/v1/sandbox/validation-scientist/context",
            headers=HEADERS,
            json={"task_id": "service-validation", "artifact_ref": artifact_ref},
        )

    assert response.status_code == 200, response.text
    context = response.json()
    assert context["status"] == "READY"
    assert tuple(row["resource_class"] for row in context["inputs"]) == ("HOLDOUT",) * 3
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.operation == "VALIDATION_CONTEXT"
    assert (request.candidate_ref, request.training_ref, request.holdout_ref) == (None, None, None)
    assert request.evidence_ref == artifact_ref


@pytest.mark.parametrize("kind, status", [("error", "FAILED"), ("timeout", "TIMEOUT")])
def test_agent_malfunction_enters_health_quarantine_without_confusing_candidate_failure(
    package, tmp_path, monkeypatch, kind, status
):
    import asyncio
    from ai_quant_os import validation_scientist_agent

    with CandidateValidationStore(tmp_path / "paper.candidate-validations.sqlite3") as archive:
        failure = archive.put_failure(package, failed_tests(package))

    async def malfunction(invocation):
        if kind == "timeout":
            await asyncio.sleep(3)
        raise RuntimeError("private validation fault")

    monkeypatch.setattr(validation_scientist_agent, "validate_candidate", malfunction)
    with TestClient(create_app(**settings(tmp_path), health_watchdog_enabled=True)) as client:
        _, receipt = run(client, failure.failure_ref)
        assert receipt["status"] == status and receipt["output_json"] is None
        incidents = client.get("/api/v1/paper/health/incidents", headers=HEADERS).json()[
            "incidents"
        ]
        assert any(
            row["code"] == "AGENT_" + status and row["agent_id"] == "A10" and row["quarantine"]
            for row in incidents
        )
        registry = client.get("/api/v1/agents/registry").json()["agents"]
        assert (
            next(
                row
                for row in registry
                if (row["agent_id"], row["agent_version"]) == ("A10", "0.2.0")
            )["lifecycle"]
            == "QUARANTINED"
        )
        assert "private validation fault" not in json.dumps(receipt)


def test_checked_candidate_failure_does_not_create_agent_health_incident(package, tmp_path):
    with CandidateValidationStore(tmp_path / "paper.candidate-validations.sqlite3") as archive:
        failure = archive.put_failure(package, failed_tests(package))
    with TestClient(create_app(**settings(tmp_path), health_watchdog_enabled=True)) as client:
        _, receipt = run(client, failure.failure_ref)
        assert receipt["status"] == "COMPLETED"
        incidents = client.get("/api/v1/paper/health/incidents", headers=HEADERS).json()[
            "incidents"
        ]
        assert not any(row["agent_id"] == "A10" for row in incidents)
