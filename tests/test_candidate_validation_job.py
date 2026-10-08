"""Original-source validation stages reserve before dispatch and refuse uncertainty."""

from hashlib import sha256
import importlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from ai_quant_os.candidate_validation_store import CandidateValidationStore
from test_candidate_dataset_views import unattested_test_metadata
from test_candidate_validation_store import failed_tests
from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon


def controller():
    assert importlib.util.find_spec("ai_quant_os.candidate_validation_job"), (
        "Durable original-source validation orchestration required"
    )
    return importlib.import_module("ai_quant_os.candidate_validation_job")


def prepared(package, scripted, tmp_path):
    root = tmp_path / "validation-job"
    root.mkdir()
    manifest = controller().prepare_validation_job(package, root=root, **scripted[1])
    return root, manifest


def test_prepare_binds_original_package_and_all_declared_intervals_without_execution(
    package, scripted, tmp_path
):
    root, manifest = prepared(package, scripted, tmp_path)
    assert manifest.package_ref == package.package_ref
    assert manifest.development_folds == 2
    assert manifest.stage_count == 5
    assert manifest.promotion_allowed is False
    assert not scripted[2]
    assert sha256((root / "validation-job.json").read_bytes()).hexdigest() == manifest.job_sha256
    assert not (root / "holdout").exists()
    with pytest.raises(ValueError):
        controller().prepare_validation_job(package, root=root, **scripted[1])


def test_failed_tests_publish_only_checked_failure_and_survive_restart(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = failed_tests(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *args: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *args: observed)
    archive_path = tmp_path / "results.sqlite3"
    with CandidateValidationStore(archive_path) as archive:
        first = controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        assert first.stage == "TESTS" and first.phase == "COMPLETED"
        final = controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        assert final.stage == "PUBLICATION" and final.phase == "COMPLETED"
        assert archive.get(final.artifact_ref).test_result == observed
    assert not (root / "holdout").exists() and not scripted[2]
    with CandidateValidationStore(archive_path) as archive:
        assert (
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
            == final
        )


def test_complete_job_uses_original_holdout_and_ordered_windows_once(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    monkeypatch.setattr(
        controller(), "run_candidate_tests", lambda *args: unattested_test_metadata(package)
    )
    monkeypatch.setattr(
        controller(), "read_candidate_test_result", lambda *args: unattested_test_metadata(package)
    )
    with CandidateValidationStore(tmp_path / "results.sqlite3") as archive:
        stages = []
        for _ in range(manifest.stage_count):
            record = controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
            stages.append(record.stage)
        assert stages == ["TESTS", "HOLDOUT", "DEVELOPMENT:0", "DEVELOPMENT:1", "PUBLICATION"]
        artifact = archive.get(record.artifact_ref)
        assert artifact.package_ref == package.package_ref and len(artifact.gates) == 15
        assert artifact.promotion_allowed is False
        calls = len(scripted[2])
        assert (
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
            == record
        )
        assert len(scripted[2]) == calls


def test_lost_stage_acknowledgement_requires_independent_result_pin_before_recovery(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = failed_tests(package)

    def uncertain(*args):
        (root / "candidate-tests/result.json").write_bytes(observed.model_dump_json().encode())
        raise RuntimeError("private lost acknowledgement")

    monkeypatch.setattr(controller(), "run_candidate_tests", uncertain)
    with CandidateValidationStore() as archive:
        with pytest.raises(RuntimeError):
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
        record = controller().validation_job_status(
            package, root=root, job_sha256=manifest.job_sha256
        )
        assert record.phase == "UNCERTAIN"
        assert "private" not in record.model_dump_json()
        monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: pytest.fail("No retry"))
        with pytest.raises(ValueError):
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
        monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
        with pytest.raises(ValueError):
            controller().recover_validation_job(
                package,
                root=root,
                job_sha256=manifest.job_sha256,
                result_sha256="0" * 64,
                archive=archive,
            )
        recovered = controller().recover_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            result_sha256=sha256(observed.model_dump_json().encode()).hexdigest(),
            archive=archive,
        )
        assert recovered.phase == "COMPLETED" and recovered.stage == "TESTS"


@pytest.mark.parametrize("change", ["pin", "journal", "package", "root"])
def test_changed_job_refuses_before_worker_dispatch(
    package, scripted, tmp_path, monkeypatch, change
):
    root, manifest = prepared(package, scripted, tmp_path)
    pin = manifest.job_sha256
    if change == "pin":
        pin = "0" * 64
    elif change == "journal":
        with sqlite3.connect(root / "validation-job.sqlite3") as database:
            database.execute("DELETE FROM audit_head")
    elif change == "package":
        package = package.model_copy(update={"captured_at": package.captured_at.replace(year=2025)})
    else:
        root.rename(tmp_path / "original")
        root.mkdir()
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: pytest.fail("No dispatch"))
    with CandidateValidationStore() as archive, pytest.raises(ValueError):
        controller().step_validation_job(package, root=root, job_sha256=pin, archive=archive)


def test_status_cli_uses_original_archive_and_refuses_wrong_pin(package, scripted, tmp_path):
    root, manifest = prepared(package, scripted, tmp_path)
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_validation_job_cli",
        "--validation-archive",
        str(tmp_path / "paper.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--root",
        str(root),
        "status",
        "--job-sha256",
        manifest.job_sha256,
    ]
    observed = subprocess.run(command, capture_output=True, text=True)
    assert observed.returncode == 0, observed.stderr
    assert json.loads(observed.stdout)["stage"] is None
    command[-1] = "0" * 64
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 2 and not rejected.stdout


def test_workflow_cli_refuses_remote_temporal_endpoint_before_dispatch(package, scripted, tmp_path):
    root, manifest = prepared(package, scripted, tmp_path)
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_validation_job_cli",
        "--validation-archive",
        str(tmp_path / "paper.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--root",
        str(root),
        "workflow",
        "--job-sha256",
        manifest.job_sha256,
        "--record-archive",
        str(tmp_path / "results.sqlite3"),
        "--run-id",
        "remote-denied",
        "--temporal-endpoint",
        "remote.example:7233",
    ]
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 2 and "loopback" in rejected.stderr
    assert not (tmp_path / "results.sqlite3").exists() and not scripted[2]


def test_failed_publication_recovery_does_not_require_native_engine(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = failed_tests(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    complete = controller()._complete

    def lost_publication(store, record, raw, **kwargs):
        if record.stage == "PUBLICATION":
            raise RuntimeError("lost publication acknowledgement")
        return complete(store, record, raw, **kwargs)

    monkeypatch.setattr(controller(), "_complete", lost_publication)
    path = tmp_path / "results.sqlite3"
    with CandidateValidationStore(path) as archive:
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        with pytest.raises(RuntimeError):
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
    (tmp_path / "package.json").write_bytes(package.model_dump_json().encode())
    program = """
import importlib.abc, sys
from pathlib import Path
class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'nautilus_trader':
            raise ImportError('Native engine deliberately absent')
sys.meta_path.insert(0, NoNative())
from ai_quant_os import candidate_validation_job as jobs
from ai_quant_os.validation_package_contracts import ValidationPackage
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_validation_store import CandidateValidationStore
package = ValidationPackage.model_validate_json(Path(sys.argv[1]).read_bytes())
root = Path(sys.argv[2])
observed = CandidateTestResult.model_validate_json((root / 'test-observation.json').read_bytes())
jobs.read_candidate_test_result = lambda *a: observed  # Scripted worker transport metadata.
with CandidateValidationStore(Path(sys.argv[4])) as archive:
    record = jobs.recover_validation_job(package, root=root, job_sha256=sys.argv[3],
        result_sha256=sys.argv[5], archive=archive)
assert record.phase == 'COMPLETED' and record.artifact_ref.startswith('candidate-prerequisite-failure:')
assert 'nautilus_trader' not in sys.modules
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(tmp_path / "package.json"),
            str(root),
            manifest.job_sha256,
            str(path),
            sha256((root / "publication-observation.json").read_bytes()).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )
    assert child.returncode == 0, child.stderr


def test_actual_linux_job_reconciles_test_ack_and_publishes_complete_scorecard(
    package, tmp_path, monkeypatch
):
    if os.environ.get("AI_QUANT_DATASET_WINDOW_ENGINE_TEST") != "1":
        pytest.skip("Actual owned Linux validation gate is opt-in")
    from test_candidate_dataset_runs import image_inputs

    root = tmp_path / "actual-validation-job"
    root.mkdir()
    manifest = controller().prepare_validation_job(package, root=root, **image_inputs())
    original = controller().run_candidate_tests

    def lost_ack(*args):
        original(*args)
        raise RuntimeError("Observed result with lost orchestration acknowledgement")

    monkeypatch.setattr(controller(), "run_candidate_tests", lost_ack)
    with CandidateValidationStore(tmp_path / "results.sqlite3") as archive:
        with pytest.raises(RuntimeError):
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
        from ai_quant_os.validation_interruption import (
            capture_validation_interruption,
            verify_validation_interruption,
        )

        interruption = capture_validation_interruption(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
        )
        (tmp_path / "validation-job-interruption.json").write_bytes(
            interruption.model_dump_json().encode()
        )
        monkeypatch.setattr(
            controller(), "run_candidate_tests", lambda *a: pytest.fail("No test restart")
        )
        observed = (root / "candidate-tests/result.json").read_bytes()
        recovered = controller().recover_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            result_sha256=sha256(observed).hexdigest(),
            archive=archive,
        )
        assert recovered.test_status == "PROPOSED_TESTS_PASSED_UNATTESTED"
        verify_validation_interruption(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            record=interruption,
        )
    import asyncio
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from ai_quant_os.temporal_validation import (
        CandidateValidationWorkflow,
        ValidationActivities,
        start_validation_workflow,
    )
    from test_temporal_recovery import temporal_binary

    async def run():
        activities = ValidationActivities(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive_path=tmp_path / "results.sqlite3",
        )
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=temporal_binary(),
            dev_server_database_filename=str(tmp_path / "temporal.sqlite3"),
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="actual-validation",
                workflows=[CandidateValidationWorkflow],
                activities=activities.registered(),
                max_concurrent_activities=1,
            ):
                handle = await start_validation_workflow(
                    environment.client,
                    activities,
                    run_id="actual-linux",
                    task_queue="actual-validation",
                )
                result = await asyncio.wait_for(handle.result(), 600)
            history = await handle.fetch_history()
            scheduled = [
                e for e in history.events if e.HasField("activity_task_scheduled_event_attributes")
            ]
            assert len(scheduled) == manifest.stage_count - 1
            assert all(
                e.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
                for e in scheduled
            )
            assert result["state"] == "ADVISORY_RESULT_RETAINED"
            assert result["promotion_allowed"] is False
            (tmp_path / "validation-job-workflow.json").write_text(
                json.dumps(
                    {
                        "workflow_id": handle.id,
                        "result": result,
                        "scheduled_activities": len(scheduled),
                        "maximum_activity_attempts": 1,
                    },
                    indent=2,
                )
                + "\n"
            )
            return result

    result = asyncio.run(run())
    with CandidateValidationStore(tmp_path / "results.sqlite3") as archive:
        final = controller().step_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive=archive,
        )
        assert final.artifact_ref == result["artifact_ref"]
        artifact = archive.get(final.artifact_ref)
        assert final.stage == "PUBLICATION" and final.phase == "COMPLETED"
        assert len(artifact.gates) == 15 and artifact.promotion_allowed is False
        (tmp_path / "validation-job-final.json").write_bytes(final.model_dump_json().encode())
        (tmp_path / "validation-job-scorecard.json").write_bytes(
            artifact.model_dump_json().encode()
        )


def test_cooperative_stop_resumes_only_remaining_owned_prefixes(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = unattested_test_metadata(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    with CandidateValidationStore() as archive:
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )

        def checkpoint():
            starts = [call for call in scripted[2] if call[:2] == ("container", "start")]
            if len(starts) == 1:
                raise controller().ValidationJobPaused("Stop requested")

        paused = controller().step_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive=archive,
            checkpoint=checkpoint,
        )
        assert paused.phase == "PAUSED" and paused.stage == "HOLDOUT"
        assert not scripted[3]
        resumed = controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        assert resumed.phase == "COMPLETED" and resumed.attempt == 1
        assert len([call for call in scripted[2] if call[:2] == ("container", "start")]) == 10


def test_repeated_cooperative_pauses_have_distinct_audit_events(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = unattested_test_metadata(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)

    def checkpoint():
        raise controller().ValidationJobPaused("Stop before dispatch")

    with CandidateValidationStore() as archive:
        for attempt in range(3):
            record = controller().step_validation_job(
                package,
                root=root,
                job_sha256=manifest.job_sha256,
                archive=archive,
                checkpoint=checkpoint,
            )
            assert record.phase == "PAUSED" and record.attempt == attempt
        completed = controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )
        assert completed.phase == "COMPLETED" and completed.attempt == 3
    assert not scripted[2]


def test_global_pause_budget_retains_uncertainty_without_dispatch(package, scripted, tmp_path):
    root, manifest = prepared(package, scripted, tmp_path)

    def checkpoint():
        raise controller().ValidationJobPaused("Stop before dispatch")

    with CandidateValidationStore() as archive:
        for attempt in range(manifest.maximum_cooperative_pauses + 1):
            record = controller().step_validation_job(
                package,
                root=root,
                job_sha256=manifest.job_sha256,
                archive=archive,
                checkpoint=checkpoint,
            )
            assert record.attempt == attempt
            assert record.phase == ("PAUSED" if attempt < 16 else "UNCERTAIN")
        with pytest.raises(ValueError):
            controller().step_validation_job(
                package,
                root=root,
                job_sha256=manifest.job_sha256,
                archive=archive,
            )
    assert not scripted[2]
