"""A10 reads checked original results through task-bound sandbox graphs."""

from datetime import timedelta
from contextlib import ExitStack
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ai_quant_os.api import create_app
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.holdout_validator_rpc import (
    HoldoutContextSnapshot,
    HoldoutResponse,
    HoldoutRpcClient,
    process_rpc_frame,
)
from ai_quant_os.holdout_validator_service import HoldoutRequestHandler
from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore
from ai_quant_os.research_store import ResearchStore
from ai_quant_os.validation_scientist_sources import ValidationContextSnapshot
from ai_quant_os.validation_package_store import ValidationPackageStore
from test_candidate_validation_store import failed_tests
from test_candidate_validation import observed_reports
from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon
from test_research_hypothesis_api import NOW, HEADERS


def settings(tmp_path, clock=lambda: NOW, holdout_rpc_client=None):
    return dict(
        operator_token="fixture-hypothesis",
        clock=clock,
        paper_db_path=tmp_path / "paper.sqlite3",
        holdout_rpc_client=holdout_rpc_client,
    )


@pytest.fixture
def isolated_service(tmp_path, package):
    del package  # Ensure the package fixture has initialized the service-owned archives.
    with ExitStack() as stack:
        holdouts = stack.enter_context(
            HoldoutEvidenceStore(tmp_path / "service.research-data.holdout.sqlite3")
        )
        packages = stack.enter_context(
            ValidationPackageStore(tmp_path / "service.validation-packages.sqlite3")
        )
        validations = stack.enter_context(
            CandidateValidationStore(tmp_path / "service.candidate-validations.sqlite3")
        )
        handler = HoldoutRequestHandler(
            holdouts, packages, validations, ResearchStore(tmp_path / "service.research.sqlite3")
        )

        class LocalTransport:
            failure = None

            def exchange(self, frame, *, timeout_ms):
                assert timeout_ms > 0
                if self.failure == "timeout":
                    import time

                    time.sleep(3)
                    raise TimeoutError("synthetic service timeout")
                if self.failure == "error":
                    raise OSError("synthetic service failure")
                return process_rpc_frame(frame, handler)

        transport = LocalTransport()
        yield HoldoutRpcClient(transport), transport, validations


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


def test_failed_candidate_completes_advisory_agent_graph_without_trading_metrics(
    package, tmp_path, isolated_service
):
    validator, _transport, validations = isolated_service
    failure = validations.put_failure(package, failed_tests(package))
    with TestClient(create_app(**settings(tmp_path, holdout_rpc_client=validator))) as client:
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
        result_url = "/api/v1/sandbox/validation-scientist/results/" + failure.failure_ref
        assert client.get(result_url).status_code == 401
        retained = client.get(result_url, headers=HEADERS)
        assert retained.status_code == 200, retained.text
        assert retained.json()["summary"]["artifact_ref"] == failure.failure_ref
        assert retained.json()["archival_only"] is True
        assert retained.json()["promotion_allowed"] is False
        assert "artifact" not in retained.json()
    with TestClient(create_app(**settings(tmp_path, holdout_rpc_client=validator))) as client:
        saved = client.get("/api/v1/agents/graph-runs/validate", headers=HEADERS)
        assert saved.json()["receipt"] == receipt
        resumed = client.post(
            "/api/v1/sandbox/validation-scientist/graph-runs/validate/resume", headers=HEADERS
        )
        assert resumed.status_code == 200 and resumed.json()["receipt"] == receipt


def test_operator_rpc_publication_recomputes_failure_in_service_archive(
    package, tmp_path, isolated_service
):
    from ai_quant_os.candidate_prerequisite_failure import record_prerequisite_failure
    from ai_quant_os.holdout_validator_rpc import HoldoutRequest, HoldoutValidationPublication
    from ai_quant_os.validation_scientist_sources import validation_summary

    validator, _transport, validations = isolated_service
    tested = failed_tests(package)
    expected = record_prerequisite_failure(package, tested)
    request = HoldoutRequest(
        request_id="publish-failure-1",
        operation="VALIDATION_PUBLISH",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="operator-publish",
        context_ref="publish-context-1",
        evidence_ref=package.package_ref,
        publication=HoldoutValidationPublication(
            kind="PREREQUISITE_FAILURE",
            package_ref=package.package_ref,
            test_result_json=tested.model_dump_json(),
        ),
    )

    response = validator.call(request)
    assert response.status == "CHECKED_VALIDATION_PUBLISHED"
    assert response.published_ref == expected.failure_ref
    assert response.summary_sha256 == validation_summary(package, expected).summary_sha256
    assert validations.get(expected.failure_ref) == expected
    assert response.aggregate.outcome == "PREREQUISITE_FAILED"
    assert response.aggregate.gate_count == 15


def test_operator_rpc_publication_refuses_changed_test_binding_without_archive_write(
    package, tmp_path, isolated_service
):
    from ai_quant_os.holdout_validator_rpc import (
        HoldoutRequest,
        HoldoutRpcError,
        HoldoutValidationPublication,
    )

    validator, _transport, validations = isolated_service
    tested = failed_tests(package).model_copy(
        update={"request_ref": "candidate-test-request:" + "0" * 64}
    )
    request = HoldoutRequest(
        request_id="publish-failure-invalid",
        operation="VALIDATION_PUBLISH",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="operator-publish-invalid",
        context_ref="publish-context-invalid",
        evidence_ref=package.package_ref,
        publication=HoldoutValidationPublication(
            kind="PREREQUISITE_FAILURE",
            package_ref=package.package_ref,
            test_result_json=tested.model_dump_json(),
        ),
    )

    with pytest.raises(HoldoutRpcError) as raised:
        validator.call(request)
    assert raised.value.error_code == "REQUEST_REFUSED"
    assert validations._db.execute("SELECT COUNT(*) FROM candidate_validations").fetchone()[0] == 0


def test_operator_rpc_recomputes_and_publishes_scorecard_from_bounded_reports(
    package, scripted, tmp_path, isolated_service
):
    from ai_quant_os.candidate_validation import evaluate_candidate
    from ai_quant_os.holdout_validator_rpc import HoldoutRequest, HoldoutValidationPublication

    tested, holdout, windows = observed_reports(package, scripted)
    validator, _transport, validations = isolated_service
    expected = evaluate_candidate(package, tested, holdout, windows)
    request = HoldoutRequest(
        request_id="publish-evaluation-1",
        operation="VALIDATION_PUBLISH",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="operator-publish-evaluation",
        context_ref="publish-evaluation-context",
        evidence_ref=package.package_ref,
        publication=HoldoutValidationPublication(
            kind="EVALUATION",
            package_ref=package.package_ref,
            test_result_json=tested.model_dump_json(),
            holdout_report_json=holdout.model_dump_json(),
            development_reports_json=tuple(report.model_dump_json() for report in windows),
        ),
    )

    response = validator.call(request)
    assert response.status == "CHECKED_VALIDATION_PUBLISHED"
    assert response.published_ref == expected.scorecard_ref
    assert validations.get(expected.scorecard_ref) == expected
    assert response.aggregate.outcome == expected.outcome
    assert response.aggregate.gate_count == len(expected.gates)
    assert len(response.model_dump_json().encode()) < 65536


def test_candidate_validation_cli_publishes_failure_without_local_package_archive(
    package, tmp_path, isolated_service, capsys
):
    from hashlib import sha256
    from ai_quant_os.candidate_validation_cli import main

    validator, _transport, validations = isolated_service
    tested = failed_tests(package)
    test_path = tmp_path / "test-result.json"
    test_path.write_bytes(tested.model_dump_json().encode())
    raw = test_path.read_bytes()

    result = main(
        [
            "--service",
            "--package-ref",
            package.package_ref,
            "--test-result",
            str(test_path),
            sha256(raw).hexdigest(),
            "archive-failure",
        ],
        holdout_client=validator,
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["failure_ref"] == validations.get(output["failure_ref"]).failure_ref
    assert output["outcome"] == "PREREQUISITE_FAILED"
    assert output["promotion_allowed"] is False


def test_candidate_validation_cli_publishes_scorecard_via_service_rpc(
    package, scripted, tmp_path, isolated_service, capsys
):
    from hashlib import sha256
    from ai_quant_os.candidate_validation import evaluate_candidate
    from ai_quant_os.candidate_validation_cli import main

    tested, holdout, windows = observed_reports(package, scripted)
    validator, _transport, validations = isolated_service
    expected = evaluate_candidate(package, tested, holdout, windows)

    def pinned(name, model):
        path = tmp_path / name
        raw = model.model_dump_json().encode()
        path.write_bytes(raw)
        return str(path), sha256(raw).hexdigest()

    test_path, test_pin = pinned("test-result.json", tested)
    holdout_path, holdout_pin = pinned("holdout-report.json", holdout)
    args = [
        "--service",
        "--package-ref",
        package.package_ref,
        "--test-result",
        test_path,
        test_pin,
        "--holdout-report",
        holdout_path,
        holdout_pin,
    ]
    for index, report in enumerate(windows):
        path, pin = pinned(f"development-{index}.json", report)
        args.extend(["--development-report", path, pin])
    args.append("archive-evaluation")

    result = main(args, holdout_client=validator)

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["scorecard_ref"] == expected.scorecard_ref
    assert validations.get(expected.scorecard_ref) == expected
    assert output["outcome"] == expected.outcome
    assert output["promotion_allowed"] is False


def test_complete_original_scorecard_is_summarized_with_every_gate(
    package, scripted, tmp_path, isolated_service
):
    tested, holdout, windows = observed_reports(package, scripted)
    validator, _transport, validations = isolated_service
    score = validations.put_evaluation(package, tested, holdout, windows)
    with TestClient(create_app(**settings(tmp_path, holdout_rpc_client=validator))) as client:
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


def test_expired_context_and_changed_archive_withhold_agent_output(
    package, tmp_path, isolated_service
):
    now = [NOW]
    validator, _transport, validations = isolated_service
    path = tmp_path / "service.candidate-validations.sqlite3"
    failure = validations.put_failure(package, failed_tests(package))
    with TestClient(
        create_app(**settings(tmp_path, clock=lambda: now[0], holdout_rpc_client=validator))
    ) as client:
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
        assert response.json()["receipt"]["status"] == "FAILED"
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
    with TestClient(create_app(**settings(tmp_path, holdout_rpc_client=client))) as api:
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
    package, tmp_path, isolated_service, kind, status
):
    validator, transport, validations = isolated_service
    failure = validations.put_failure(package, failed_tests(package))
    with TestClient(
        create_app(**settings(tmp_path, holdout_rpc_client=validator), health_watchdog_enabled=True)
    ) as client:
        context_response = client.post(
            "/api/v1/sandbox/validation-scientist/context",
            headers=HEADERS,
            json={"task_id": "validate", "artifact_ref": failure.failure_ref},
        )
        assert context_response.status_code == 200, context_response.text
        transport.failure = kind
        response = client.post(
            "/api/v1/sandbox/validation-scientist/execute",
            headers=HEADERS,
            json={"run_id": "validate", "context_ref": context_response.json()["context_ref"]},
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
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


def test_checked_candidate_failure_does_not_create_agent_health_incident(
    package, tmp_path, isolated_service
):
    validator, _transport, validations = isolated_service
    failure = validations.put_failure(package, failed_tests(package))
    with TestClient(
        create_app(**settings(tmp_path, holdout_rpc_client=validator), health_watchdog_enabled=True)
    ) as client:
        _, receipt = run(client, failure.failure_ref)
        assert receipt["status"] == "COMPLETED"
        incidents = client.get("/api/v1/paper/health/incidents", headers=HEADERS).json()[
            "incidents"
        ]
        assert not any(row["agent_id"] == "A10" for row in incidents)
