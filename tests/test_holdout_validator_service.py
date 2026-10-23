from pathlib import Path
import sqlite3

from ai_quant_os.holdout_validator_service import (
    HoldoutRequestHandler,
    WindowsNamedPipeServer,
    _require_service_holdout,
    _ensure_service_registered,
    _remove_service,
    stage_service_archives,
)
from ai_quant_os.holdout_service_security import current_process_user_sid
from ai_quant_os.holdout_validator_rpc import (
    HoldoutRequest,
    HoldoutResponse,
    _RESPONSE_RECEIVED_ACK,
    decode_frame,
    encode_frame,
)


class FakeServiceApi:
    SC_MANAGER_CONNECT = 1
    SERVICE_QUERY_STATUS = 2
    SERVICE_STOPPED = 1
    SERVICE_AUTO_START = 2
    ERROR_SERVICE_DOES_NOT_EXIST = 1060

    def __init__(self, state=None):
        self.state = state
        self.closed = []

    def OpenSCManager(self, machine, database, access):
        assert (machine, database, access) == (None, None, self.SC_MANAGER_CONNECT)
        return "manager"

    def OpenService(self, manager, name, access):
        assert (manager, name, access) == ("manager", "AIQuantHoldout", 2)
        if self.state is None:
            error = OSError("missing service")
            error.winerror = self.ERROR_SERVICE_DOES_NOT_EXIST
            raise error
        return "service"

    def QueryServiceStatus(self, service):
        assert service == "service"
        return (0, self.state, 0, 0, 0, 0, 0)

    def CloseServiceHandle(self, handle):
        self.closed.append(handle)


class FakeServiceUtil:
    def __init__(self):
        self.calls = []

    def StopService(self, name):
        self.calls.append(("stop", name))

    def ChangeServiceConfig(self, *args, **kwargs):
        self.calls.append(("change", args, kwargs))

    def InstallService(self, *args, **kwargs):
        self.calls.append(("install", args, kwargs))

    def RemoveService(self, name):
        self.calls.append(("remove", name))


def test_new_service_registration_is_localservice_and_automatic():
    api, util = FakeServiceApi(), FakeServiceUtil()

    _ensure_service_registered(api, util)

    assert len(util.calls) == 1 and util.calls[0][0] == "install"
    assert util.calls[0][2]["userName"] == "NT AUTHORITY\\LocalService"
    assert util.calls[0][2]["startType"] == api.SERVICE_AUTO_START
    assert api.closed == ["manager"]


def test_existing_service_is_stopped_before_idempotent_upgrade():
    api, util = FakeServiceApi(state=4), FakeServiceUtil()

    _ensure_service_registered(api, util)

    assert util.calls[0] == ("stop", "AIQuantHoldout")
    assert util.calls[1][0] == "change"
    assert util.calls[1][2]["userName"] == "NT AUTHORITY\\LocalService"
    assert api.closed == ["service", "manager"]


def test_existing_stopped_service_does_not_stop_again():
    api, util = FakeServiceApi(state=api_state_stopped()), FakeServiceUtil()

    _ensure_service_registered(api, util)

    assert [call[0] for call in util.calls] == ["change"]


def api_state_stopped():
    return FakeServiceApi.SERVICE_STOPPED


def test_pipe_server_rejects_unsafe_configuration():
    try:
        WindowsNamedPipeServer("S-1-5-80-bad", "S-1-5-21-1-2-3-4")
    except ValueError:
        return
    raise AssertionError("Malformed service identity was accepted")


def test_remove_stops_service_but_keeps_its_data_and_is_repeatable():
    api, util = FakeServiceApi(state=4), FakeServiceUtil()

    _remove_service(service_api=api, service_util=util)
    _remove_service(service_api=FakeServiceApi(), service_util=FakeServiceUtil())

    assert util.calls == [("stop", "AIQuantHoldout"), ("remove", "AIQuantHoldout")]


def test_legacy_migration_command_reports_migration_without_acl_claim(
    tmp_path, monkeypatch, capsys
):
    from ai_quant_os import holdout_validator_service as service

    catalog = tmp_path / "research.sqlite3"
    catalog.touch()
    monkeypatch.setattr(service, "migrate_legacy_holdout_catalog", lambda _path: (object(),))

    assert service.main(["migrate-legacy-catalog", "--catalog", str(catalog)]) == 0
    output = __import__("json").loads(capsys.readouterr().out)
    assert output == {
        "catalog": str(catalog),
        "holdout_receipts_migrated": 1,
        "state": "OFFLINE_MIGRATION_COMPLETE",
        "acl_secured": False,
    }


def test_service_handler_refuses_validation_scope_before_reading_holdout():
    class Artifact:
        package_ref = "package"
        candidate_ref = "candidate-other"

    class Package:
        class Candidate:
            candidate_ref = "candidate-requested"

        class Receipt:
            dataset_ref = "dataset"

        candidate = Candidate()
        training = type("Snapshot", (), {"receipt": Receipt()})()
        holdout = type("Snapshot", (), {"receipt": Receipt()})()

    class Validations:
        def get(self, _reference):
            return Artifact()

    class Packages:
        def get(self, _reference):
            return Package()

    class Holdouts:
        def get(self, _reference):
            raise AssertionError("scope mismatch must be rejected before HOLDOUT lookup")

    handler = HoldoutRequestHandler(Holdouts(), Packages(), Validations(), research=None)
    request = HoldoutRequest(
        request_id="service-test",
        operation="VALIDATION_RUN",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="service-task",
        context_ref="service-context",
        candidate_ref="candidate-requested",
        training_ref="dataset",
        holdout_ref="dataset",
        evidence_ref="archive-summary",
    )
    with __import__("pytest").raises(PermissionError, match="scope mismatch"):
        handler(request)


def test_service_publishes_checked_holdout_bytes_and_returns_receipt_only(tmp_path):
    import base64
    from hashlib import sha256
    from uuid import uuid4

    import pytest

    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.holdout_validator_rpc import verify_response
    from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore, ResearchEvidenceCatalog
    from ai_quant_os.validation_package_store import ValidationPackageStore
    from test_research_evidence_catalog import NOW, raw_bars

    source_path = tmp_path / "service-source.sqlite3"
    with ResearchEvidenceCatalog(source_path):
        pass
    with ResearchEvidenceCatalog(tmp_path / "training-source.sqlite3") as training_catalog:
        training_receipt = training_catalog.publish(
            raw_bars(),
            source_id="training-source",
            resource_class="TRAINING",
            received_at=NOW,
        )
    holdout_path = source_path.with_name("service-source.holdout.sqlite3")
    with (
        HoldoutEvidenceStore(holdout_path) as holdouts,
        ValidationPackageStore(tmp_path / "packages.sqlite3") as packages,
        CandidateValidationStore(tmp_path / "validations.sqlite3") as validations,
    ):
        handler = HoldoutRequestHandler(holdouts, packages, validations, research=None)
        raw = raw_bars(minute=10)
        publication = {
            "source_id": "sealed-feed",
            "raw_base64": base64.b64encode(raw).decode(),
            "raw_sha256": sha256(raw).hexdigest(),
            "training_receipts_json": (training_receipt.model_dump_json(),),
        }
        request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="HOLDOUT_PUBLISH",
            agent_id="A10",
            agent_version="0.2.0",
            task_id="operator-holdout-ingest",
            context_ref="holdout-ingest:" + uuid4().hex,
            evidence_ref="holdout-ingest:" + publication["raw_sha256"],
            holdout_publication=publication,
        )
        response = handler(request)
        verify_response(request, response)
        stored = holdouts.get(response.published_ref)
        assert stored is not None and stored[1] == raw
        assert raw.decode() not in response.model_dump_json()
        retry = handler(request)
        assert retry.receipt_json == response.receipt_json

        overlap_raw = raw_bars()
        overlap = dict(
            publication,
            source_id="overlap",
            raw_base64=base64.b64encode(overlap_raw).decode(),
            raw_sha256=sha256(overlap_raw).hexdigest(),
        )
        overlap_request = HoldoutRequest.model_validate(
            request.model_dump(mode="python")
            | {
                "request_id": uuid4().hex,
                "evidence_ref": "holdout-ingest:" + overlap["raw_sha256"],
                "holdout_publication": overlap,
            }
        )
        with pytest.raises(PermissionError):
            handler(overlap_request)


def test_validation_context_reply_contains_only_checked_summary_and_holdout_receipt():
    from datetime import timedelta
    from hashlib import sha256
    from types import SimpleNamespace

    from ai_quant_os.candidate_validation_contracts import GATE_NAMES
    from ai_quant_os.holdout_validator_rpc import verify_response
    from ai_quant_os.research_data_contracts import ResearchDataReceipt, research_dataset_ref
    from ai_quant_os.validation_scientist_contracts import (
        ValidationGateSummary,
        ValidationSummary,
    )
    from ai_quant_os.validation_scientist_sources import ValidationContextSnapshot
    from test_research_hypothesis_api import NOW

    raw_sha256 = sha256(b"service holdout receipt").hexdigest()
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
    summary = ValidationSummary(
        artifact_ref="candidate-prerequisite-failure:" + "2" * 64,
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
    package = SimpleNamespace(
        holdout=SimpleNamespace(receipt=receipt), captured_at=NOW - timedelta(seconds=10)
    )
    handler = HoldoutRequestHandler(None, None, None, None)
    handler._checked_validation = lambda _request: (package, object(), summary)
    request = HoldoutRequest(
        request_id="context-request",
        operation="VALIDATION_CONTEXT",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="context-task",
        context_ref="context-read",
        evidence_ref=summary.artifact_ref,
    )

    response = handler(request)
    verify_response(request, response)
    snapshot = ValidationContextSnapshot.model_validate_json(response.context_snapshot.payload_json)
    assert snapshot.summary == summary
    assert snapshot.holdout_receipt == receipt
    assert response.summary_sha256 == summary.summary_sha256


def test_stress_context_reply_contains_checked_observations_without_report_bars():
    from hashlib import sha256
    import json
    from pathlib import Path
    from pydantic import TypeAdapter

    from ai_quant_os.adversarial_agent import AdversarialContextSnapshot
    from ai_quant_os.holdout_validator_rpc import (
        HoldoutScenarioAggregate,
        stress_summary_sha256,
        verify_response,
    )
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import encode_research

    payload = json.loads(
        (
            Path(__file__).parents[1] / "docs/evaluations/research-adversarial-baseline.json"
        ).read_text()
    )
    report = TypeAdapter(ResearchReport).validate_python(payload["report"])
    digest = sha256(encode_research(report)).hexdigest()
    scenarios = tuple(
        HoldoutScenarioAggregate(
            name=row.name,
            dataset_fingerprint=row.dataset_fingerprint,
            net_pnl=row.run.result.net_pnl,
            pnl_currency=row.run.result.pnl_currency,
            native_order_count=row.run.result.native_order_count,
        )
        for row in report.adversarial.scenarios
    )
    handler = HoldoutRequestHandler(None, None, None, None)
    handler._checked_stress = lambda _request: (
        report,
        digest,
        scenarios,
        report.adversarial.reason,
    )
    request = HoldoutRequest(
        request_id="stress-context-request",
        operation="STRESS_CONTEXT",
        agent_id="A11",
        agent_version="0.2.0",
        task_id="stress-context-task",
        context_ref="context-read",
        evidence_ref=report.plan.experiment_id,
    )

    response = handler(request)
    verify_response(request, response)
    snapshot = AdversarialContextSnapshot.model_validate_json(
        response.context_snapshot.payload_json
    )
    assert snapshot.candidate.report_sha256 == digest
    assert snapshot.stress.state == report.adversarial.state
    assert response.summary_sha256 == stress_summary_sha256(
        snapshot.stress.state, snapshot.stress.reason, snapshot.stress.scenarios
    )
    assert not any(
        field in response.context_snapshot.payload_json
        for field in ("source_bars", "fills", "raw_base64")
    )


def test_stress_report_requires_a_matching_service_owned_holdout_receipt():
    from types import SimpleNamespace

    report = SimpleNamespace(
        plan=SimpleNamespace(
            holdout_scope=SimpleNamespace(
                dataset_fingerprint="a" * 64,
                instrument_id="EURUSD",
                first_observed_at=object(),
                last_observed_at=object(),
            )
        ),
        adversarial=SimpleNamespace(source_bars=(object(), object())),
    )

    class Holdouts:
        def list(self):
            return ()

        def get(self, _reference):
            raise AssertionError("No receipt was available")

    with __import__("pytest").raises(PermissionError, match="receipt unavailable"):
        _require_service_holdout(report, Holdouts())


def test_service_prepares_and_retains_validation_package_without_returning_holdout_bytes(tmp_path):
    import base64
    import json
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from ai_quant_os.api import create_app
    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.graph_ledger import GraphLedger
    from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore, ResearchEvidenceCatalog
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_candidate_contracts import CandidateBundle
    from ai_quant_os.validation_package_contracts import ValidationPlan
    from ai_quant_os.validation_package_store import ValidationPackageStore
    from test_research_hypothesis_api import NOW, training_csv
    from test_strategy_builder_api import build, review

    paper_db = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(operator_token="fixture-hypothesis", clock=lambda: NOW, paper_db_path=paper_db)
    ) as client:
        reviewed = review(client)
        _, _, output, candidate_payload = build(client, reviewed, task="validation-build")
        with GraphLedger.archive(paper_db.with_name("paper.agents.sqlite3")) as graph_archive:
            graph = graph_archive.get("validation-build")
    candidate = CandidateBundle.model_validate(candidate_payload)

    service_research = tmp_path / "service.research-data.sqlite3"
    holdout_csv = training_csv().replace("2026-10-01", "2026-10-02").encode()
    training_bytes = training_csv().encode()
    with ResearchEvidenceCatalog(service_research) as catalog:
        training_receipt = catalog.publish(
            training_bytes,
            source_id="synthetic-development",
            resource_class="TRAINING",
            received_at=NOW,
        )
        holdout_receipt = catalog.publish(
            holdout_csv,
            source_id="later-holdout",
            resource_class="HOLDOUT",
            received_at=NOW,
        )
    assert candidate.review.hypothesis.dataset_ref == training_receipt.dataset_ref

    research = ResearchStore(tmp_path / "service.research.sqlite3")
    with (
        HoldoutEvidenceStore(
            service_research.with_name("service.research-data.holdout.sqlite3")
        ) as holdouts,
        ValidationPackageStore(tmp_path / "service.validation-packages.sqlite3") as packages,
        CandidateValidationStore(tmp_path / "service.candidate-validations.sqlite3") as validations,
    ):
        handler = HoldoutRequestHandler(holdouts, packages, validations, research)
        request = HoldoutRequest.model_validate(
            {
                "request_id": uuid4().hex,
                "operation": "VALIDATION_PREPARE",
                "agent_id": "A10",
                "agent_version": "0.2.0",
                "task_id": graph.receipt.task_id,
                "context_ref": graph.receipt.context_ref,
                "candidate_ref": output["candidate_ref"],
                "training_ref": training_receipt.dataset_ref,
                "holdout_ref": holdout_receipt.dataset_ref,
                "evidence_ref": graph.receipt.run_id,
                "preparation": {
                    "graph_json": graph.model_dump_json(),
                    "candidate_json": candidate.model_dump_json(),
                    "training_receipt_json": training_receipt.model_dump_json(),
                    "training_raw_base64": base64.b64encode(training_bytes).decode(),
                    "plan_json": ValidationPlan.model_validate(
                        {
                            "plan_id": "direction-validation",
                            "rationale": "Synthetic development check.",
                            "spread_bps": "2",
                            "slippage_bps": "1",
                            "commission_bps": "0.5",
                            "cost_stress_multiplier": "2",
                            "walk_forward_train_bars": 8,
                            "walk_forward_test_bars": 2,
                            "monte_carlo_trials": 1000,
                            "random_seed": 27,
                        }
                    ).model_dump_json(),
                    "captured_at": NOW,
                },
            }
        )
        response = handler(request)

        assert response.status == "CHECKED_VALIDATION_PACKAGE_PREPARED"
        assert response.evidence_ref == graph.receipt.run_id
        assert "raw_base64" not in response.model_dump_json()
        package_ref = json.loads(response.package_metadata_json)["package_ref"]
        package = packages.get(package_ref)
        assert package is not None
        assert base64.b64decode(package.holdout.raw_base64) == holdout_csv


def test_stage_service_archives_verifies_copies_and_preserves_sources(tmp_path):
    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.validation_package_store import ValidationPackageStore

    sources = tmp_path / "sources"
    sources.mkdir()
    holdout_catalog = sources / "research-data.sqlite3"
    with ResearchEvidenceCatalog(holdout_catalog):
        pass
    holdout_source = holdout_catalog.with_name("research-data.holdout.sqlite3")
    package_source = sources / "paper.validation-packages.sqlite3"
    candidate_source = sources / "paper.candidate-validations.sqlite3"
    research_source = sources / "research.sqlite3"
    with ValidationPackageStore(package_source):
        pass
    with CandidateValidationStore(candidate_source):
        pass
    ResearchStore(research_source)
    source_bytes = {
        path: path.read_bytes()
        for path in (holdout_source, package_source, candidate_source, research_source)
    }

    output = stage_service_archives(
        (tmp_path / "service").resolve(),
        holdout_source=holdout_source.resolve(),
        validation_packages_source=package_source.resolve(),
        candidate_validations_source=candidate_source.resolve(),
        research_source=research_source.resolve(),
    )

    assert set(output) == {
        "research-data.holdout.sqlite3",
        "validation-packages.sqlite3",
        "candidate-validations.sqlite3",
        "research.sqlite3",
    }
    assert all(Path(path).is_file() for path in output.values())
    assert all(
        path.is_file()
        for path in (holdout_source, package_source, candidate_source, research_source)
    )
    assert all(path.read_bytes() == content for path, content in source_bytes.items())
    with __import__("pytest").raises(FileExistsError, match="must be empty"):
        stage_service_archives(
            (tmp_path / "service").resolve(),
            holdout_source=holdout_source.resolve(),
            validation_packages_source=package_source.resolve(),
            candidate_validations_source=candidate_source.resolve(),
            research_source=research_source.resolve(),
        )

    unrelated_dir = (tmp_path / "unrelated-service-data").resolve()
    unrelated_dir.mkdir()
    unrelated = unrelated_dir / "operator-note.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    with __import__("pytest").raises(FileExistsError, match="must be empty"):
        stage_service_archives(
            unrelated_dir,
            holdout_source=holdout_source.resolve(),
            validation_packages_source=package_source.resolve(),
            candidate_validations_source=candidate_source.resolve(),
            research_source=research_source.resolve(),
        )
    assert unrelated.read_text(encoding="utf-8") == "preserve"

    invalid_research = sources / "invalid-research.sqlite3"
    invalid_research.write_bytes(b"not a SQLite database")
    failed_stage = (tmp_path / "failed-stage").resolve()
    with __import__("pytest").raises(sqlite3.DatabaseError):
        stage_service_archives(
            failed_stage,
            holdout_source=holdout_source.resolve(),
            validation_packages_source=package_source.resolve(),
            candidate_validations_source=candidate_source.resolve(),
            research_source=invalid_research.resolve(),
        )
    assert list(failed_stage.iterdir()) == []


def test_service_install_secures_staging_directory_before_copy_and_never_starts_on_acl_failure(
    tmp_path, monkeypatch
):
    import sys
    from types import ModuleType

    import pytest
    import ai_quant_os.holdout_validator_service as service

    if __import__("os").name != "nt":
        pytest.skip("HOLDOUT service installation is Windows-only")

    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.validation_package_store import ValidationPackageStore

    source_dir = (tmp_path / "sources").resolve()
    source_dir.mkdir()
    paper_db = source_dir / "paper.sqlite3"
    research_workspace = (source_dir / "research-workspace").resolve()
    research_workspace.mkdir()
    with ResearchEvidenceCatalog(source_dir / "paper.research-data.sqlite3"):
        pass
    with ValidationPackageStore(source_dir / "paper.validation-packages.sqlite3"):
        pass
    with CandidateValidationStore(source_dir / "paper.candidate-validations.sqlite3"):
        pass
    ResearchStore(research_workspace / "research.sqlite3")
    sources = (
        source_dir / "paper.research-data.holdout.sqlite3",
        source_dir / "paper.validation-packages.sqlite3",
        source_dir / "paper.candidate-validations.sqlite3",
        research_workspace / "research.sqlite3",
    )
    source_bytes = {path: path.read_bytes() for path in sources}

    events = []
    fake_service = ModuleType("win32service")
    fake_service_util = ModuleType("win32serviceutil")
    fake_service_util.StartService = lambda name: events.append(("start", name))
    monkeypatch.setitem(sys.modules, "win32service", fake_service)
    monkeypatch.setitem(sys.modules, "win32serviceutil", fake_service_util)
    monkeypatch.setattr(service, "_ensure_service_registered", lambda *_: events.append("register"))
    monkeypatch.setattr(
        service, "configure_holdout_service_sid", lambda **_: events.append("service-sid")
    )
    monkeypatch.setattr(service, "current_process_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    real_stage = service.stage_service_archives

    def track_stage(*args, **kwargs):
        events.append("stage")
        return real_stage(*args, **kwargs)

    monkeypatch.setattr(service, "stage_service_archives", track_stage)
    monkeypatch.setattr(
        service,
        "_write_service_settings",
        lambda *_: events.append("settings"),
    )
    acl_fail_on_call = None
    acl_call_count = 0

    def verify_acl(data_dir, *, adopt_existing, staging_operator_sid=None):
        nonlocal acl_call_count
        acl_call_count += 1
        data_dir.mkdir(parents=True, exist_ok=True)
        events.append(("acl", adopt_existing, staging_operator_sid))
        archives = tuple(
            data_dir / name
            for name in (
                "research-data.holdout.sqlite3",
                "validation-packages.sqlite3",
                "candidate-validations.sqlite3",
                "research.sqlite3",
            )
        )
        if any(path.exists() for path in archives):
            assert all(path.is_file() for path in archives)
        else:
            assert all(not path.exists() for path in archives)
        if acl_call_count == acl_fail_on_call:
            raise PermissionError("synthetic ACL acceptance failure")

    monkeypatch.setattr(service, "secure_holdout_directory", verify_acl)

    good_data_dir = (tmp_path / "service-good").resolve()
    service._install_service(
        good_data_dir,
        paper_db=paper_db,
        research_workspace=research_workspace,
    )
    assert events == [
        "register",
        "service-sid",
        ("acl", False, "S-1-5-21-1-2-3-1001"),
        "stage",
        "settings",
        ("acl", True, None),
        ("start", "AIQuantHoldout"),
    ]
    assert all(path.read_bytes() == content for path, content in source_bytes.items())

    events.clear()
    acl_call_count = 0
    existing_data_dir = (tmp_path / "service-existing").resolve()
    existing_data_dir.mkdir()
    archive_names = (
        "research-data.holdout.sqlite3",
        "validation-packages.sqlite3",
        "candidate-validations.sqlite3",
        "research.sqlite3",
    )
    for name, source in zip(archive_names, sources, strict=True):
        (existing_data_dir / name).write_bytes(source.read_bytes())
    service._install_service(
        existing_data_dir,
        paper_db=paper_db,
        research_workspace=research_workspace,
    )
    assert events == [
        "register",
        "service-sid",
        "settings",
        ("acl", True, None),
        ("start", "AIQuantHoldout"),
    ]

    events.clear()
    acl_call_count = 0
    acl_fail_on_call = 1
    failed_data_dir = (tmp_path / "service-failed").resolve()
    with pytest.raises(PermissionError, match="synthetic ACL"):
        service._install_service(
            failed_data_dir,
            paper_db=paper_db,
            research_workspace=research_workspace,
        )
    assert events[-1] == ("acl", False, "S-1-5-21-1-2-3-1001")
    assert not any(event == "stage" for event in events)
    assert list(failed_data_dir.iterdir()) == []
    assert not any(isinstance(event, tuple) and event[0] == "start" for event in events)
    assert all(path.read_bytes() == content for path, content in source_bytes.items())

    events.clear()
    acl_call_count = 0
    acl_fail_on_call = 2
    final_acl_failure_dir = (tmp_path / "service-final-acl-failed").resolve()
    with pytest.raises(PermissionError, match="synthetic ACL"):
        service._install_service(
            final_acl_failure_dir,
            paper_db=paper_db,
            research_workspace=research_workspace,
        )
    assert "stage" in events
    assert all(
        (final_acl_failure_dir / name).is_file()
        for name in (
            "research-data.holdout.sqlite3",
            "validation-packages.sqlite3",
            "candidate-validations.sqlite3",
            "research.sqlite3",
        )
    )
    assert not any(isinstance(event, tuple) and event[0] == "start" for event in events)
    assert all(path.read_bytes() == content for path, content in source_bytes.items())


@__import__("pytest").mark.skipif(__import__("os").name != "nt", reason="Windows named-pipe test")
def test_named_pipe_server_transfers_one_checked_bounded_response():
    import threading
    import time
    import uuid

    import pywintypes
    import win32con
    import win32event
    import win32file
    import win32pipe

    pipe_name = rf"\\.\pipe\AIQuantHoldout.Integration.{uuid.uuid4().hex}"
    service_sid = "S-1-5-80-1-2-3-4-5"
    operator_sid = current_process_user_sid()
    stopping = win32event.CreateEvent(None, True, False, None)
    failures = []
    server = WindowsNamedPipeServer(
        service_sid,
        operator_sid,
        pipe_name=pipe_name,
        failure_reporter=failures.append,
        one_shot=True,
    )
    completed = threading.Event()
    handler_called = threading.Event()

    def handler(request):
        handler_called.set()
        return HoldoutResponse(
            request_id=request.request_id,
            task_id=request.task_id,
            context_ref=request.context_ref,
            status="CHECKED_ARCHIVED_OBSERVATION",
            evidence_ref=request.evidence_ref,
            summary_sha256="c" * 64,
            aggregate={"outcome": "REJECTED", "gate_count": 5},
        )

    def serve():
        server.serve(stopping, handler)
        completed.set()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    handle = None
    try:
        deadline = time.monotonic() + 3
        while handle is None and time.monotonic() < deadline:
            try:
                win32pipe.WaitNamedPipe(pipe_name, 200)
                handle = win32file.CreateFile(
                    pipe_name,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
            except (OSError, pywintypes.error):
                time.sleep(0.02)
        assert handle is not None, "pipe server did not become available"
        request = HoldoutRequest.model_validate(
            {
                "request_id": "pipe-request-1",
                "operation": "VALIDATION_RUN",
                "agent_id": "A10",
                "agent_version": "0.2.0",
                "task_id": "pipe-task-1",
                "context_ref": "pipe-context-1",
                "candidate_ref": "candidate-1",
                "training_ref": "training-1",
                "holdout_ref": "holdout-1",
                "evidence_ref": "archive-summary-001",
            }
        )
        frame = encode_frame(request)
        _error, written = win32file.WriteFile(handle, frame)
        assert written == len(frame)
        header = _read_pipe_exact(handle, 4, win32file)
        length = int.from_bytes(header, "big")
        assert 1 <= length <= 65536
        try:
            response_bytes = _read_pipe_exact(handle, length, win32file)
        except (OSError, pywintypes.error) as error:
            raise AssertionError(f"server failure categories: {failures!r}") from error
        response = decode_frame(header + response_bytes, HoldoutResponse)
        assert response.task_id == request.task_id
        _error, written = win32file.WriteFile(handle, _RESPONSE_RECEIVED_ACK)
        assert written == len(_RESPONSE_RECEIVED_ACK)
        assert completed.wait(1), "server did not finish the acknowledged exchange"
    finally:
        if handle is not None:
            win32file.CloseHandle(handle)
        win32event.SetEvent(stopping)
        worker.join(3)
        win32file.CloseHandle(stopping)
    assert completed.is_set()
    assert handler_called.is_set()
    assert failures == []


def _read_pipe_exact(handle, size, win32file):
    content = bytearray()
    while len(content) < size:
        _error, chunk = win32file.ReadFile(handle, size - len(content))
        if not chunk:
            raise AssertionError("named pipe closed before the complete frame arrived")
        content.extend(chunk)
    return bytes(content)
