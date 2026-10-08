"""Interrupted validation retains original audited uncertainty without execution."""

import importlib
import json
import sqlite3
import subprocess
import sys

import pytest

from ai_quant_os.candidate_validation_store import CandidateValidationStore
from test_candidate_validation_job import prepared, controller
from test_candidate_validation_store import failed_tests
from test_candidate_dataset_views import unattested_test_metadata
from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon


def interruptions():
    assert importlib.util.find_spec("ai_quant_os.validation_interruption"), (
        "Checked original-stage interruption reports required"
    )
    return importlib.import_module("ai_quant_os.validation_interruption")


def interrupted(package, scripted, tmp_path, monkeypatch):
    root, manifest = prepared(package, scripted, tmp_path)

    def unknown(*args):
        raise RuntimeError("private exception text must not enter report")

    monkeypatch.setattr(controller(), "run_candidate_tests", unknown)
    with CandidateValidationStore() as archive, pytest.raises(RuntimeError):
        controller().step_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive=archive,
        )
    return root, manifest


def test_interruption_binds_original_unknown_stage_without_retry(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = interrupted(package, scripted, tmp_path, monkeypatch)
    module = interruptions()
    record = module.capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    assert record.package_ref == package.package_ref and record.job_sha256 == manifest.job_sha256
    assert record.stage.phase == "UNCERTAIN" and record.stage.stage == "TESTS"
    assert record.boundary == "PROPOSED_TEST_EXECUTION"
    assert record.outcome == "VALIDATION_INTERRUPTED" and record.recovery_required is True
    assert record.promotion_allowed is False and record.cleanup_verified is False
    assert "private exception" not in record.model_dump_json() and not scripted[2]
    assert (
        module.capture_validation_interruption(package, root=root, job_sha256=manifest.job_sha256)
        == record
    )
    with CandidateValidationStore() as archive, pytest.raises(ValueError):
        controller().step_validation_job(
            package, root=root, job_sha256=manifest.job_sha256, archive=archive
        )


def test_interruption_stays_historical_after_pinned_reconciliation(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = interrupted(package, scripted, tmp_path, monkeypatch)
    module = interruptions()
    record = module.capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    observed = failed_tests(package)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)
    from hashlib import sha256

    with CandidateValidationStore() as archive:
        controller().recover_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            result_sha256=sha256(observed.model_dump_json().encode()).hexdigest(),
            archive=archive,
        )
    module.verify_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256, record=record
    )
    assert record.stage.phase == "UNCERTAIN" and not scripted[2]
    with pytest.raises(ValueError):
        module.capture_validation_interruption(package, root=root, job_sha256=manifest.job_sha256)


@pytest.mark.parametrize(
    "change", ["head", "count", "stage", "authority", "statistics", "job", "boundary"]
)
def test_changed_interruption_refuses_original_audit(
    package, scripted, tmp_path, monkeypatch, change
):
    root, manifest = interrupted(package, scripted, tmp_path, monkeypatch)
    module = interruptions()
    record = module.capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    updates = {
        "head": {"audit_head_sha256": "0" * 64},
        "count": {"audit_event_count": 1},
        "stage": {"stage": record.stage.model_copy(update={"stage": "HOLDOUT"})},
        "authority": {"promotion_allowed": True},
        "statistics": {"net_pnl": "10"},
        "job": {"job_sha256": "0" * 64},
        "boundary": {"boundary": "RESULT_PUBLICATION"},
    }[change]
    with pytest.raises(ValueError):
        module.verify_validation_interruption(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            record=record.model_copy(update=updates),
        )
    assert not scripted[2]


def test_changed_original_audit_withholds_historical_report(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = interrupted(package, scripted, tmp_path, monkeypatch)
    module = interruptions()
    record = module.capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    with sqlite3.connect(root / "validation-job.sqlite3") as connection:
        connection.execute("UPDATE audit_head SET head_hash=?", ("0" * 64,))
    with pytest.raises(ValueError):
        module.verify_validation_interruption(
            package, root=root, job_sha256=manifest.job_sha256, record=record
        )
    assert not scripted[2]


def test_new_job_cannot_invent_interruption(package, scripted, tmp_path):
    root, manifest = prepared(package, scripted, tmp_path)
    with pytest.raises(ValueError):
        interruptions().capture_validation_interruption(
            package, root=root, job_sha256=manifest.job_sha256
        )


@pytest.mark.parametrize(
    "index,boundary",
    [
        (1, "HOLDOUT_EXECUTION"),
        (2, "DEVELOPMENT_EXECUTION"),
        (4, "RESULT_PUBLICATION"),
    ],
)
def test_original_non_test_boundary_is_retained_without_new_dispatch(
    package,
    scripted,
    tmp_path,
    monkeypatch,
    index,
    boundary,
):
    root, manifest = prepared(package, scripted, tmp_path)
    observed = unattested_test_metadata(package)
    monkeypatch.setattr(controller(), "run_candidate_tests", lambda *a: observed)
    monkeypatch.setattr(controller(), "read_candidate_test_result", lambda *a: observed)

    def stopped():
        raise RuntimeError("Unknown stage interruption")

    with CandidateValidationStore() as archive:
        for _ in range(index):
            controller().step_validation_job(
                package, root=root, job_sha256=manifest.job_sha256, archive=archive
            )
        with pytest.raises(RuntimeError):
            controller().step_validation_job(
                package,
                root=root,
                job_sha256=manifest.job_sha256,
                archive=archive,
                checkpoint=stopped,
            )
    before = tuple(scripted[2])
    module = interruptions()
    record = module.capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    assert record.stage.index == index and record.boundary == boundary
    module.verify_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256, record=record
    )
    assert tuple(scripted[2]) == before and record.cleanup_verified is False


def test_unacknowledged_prepared_stage_survives_operator_loss(package, scripted, tmp_path):
    root, manifest = prepared(package, scripted, tmp_path)

    def interrupted_operator():
        raise SystemExit("Lost operator")

    with CandidateValidationStore() as archive, pytest.raises(SystemExit):
        controller().step_validation_job(
            package,
            root=root,
            job_sha256=manifest.job_sha256,
            archive=archive,
            checkpoint=interrupted_operator,
        )
    record = interruptions().capture_validation_interruption(
        package, root=root, job_sha256=manifest.job_sha256
    )
    assert record.stage.phase == "PREPARED" and record.recovery_required is True
    assert not scripted[2]


def test_fresh_process_interruption_commands_use_independent_bytes(
    package, scripted, tmp_path, monkeypatch
):
    root, manifest = interrupted(package, scripted, tmp_path, monkeypatch)
    output = tmp_path / "interruption.json"
    no_native = """
import importlib.abc, sys
class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'nautilus_trader':
            raise ImportError('Native engine deliberately unavailable')
sys.meta_path.insert(0, NoNative())
from ai_quant_os.candidate_validation_job_cli import main
sys.argv = ['validation-job'] + sys.argv[1:]
raise SystemExit(main())
"""
    command = [
        sys.executable,
        "-c",
        no_native,
        "--validation-archive",
        str(tmp_path / "paper.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--root",
        str(root),
    ]
    captured = subprocess.run(
        command + ["interruption", "--job-sha256", manifest.job_sha256, "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert captured.returncode == 0, captured.stderr
    original = output.read_bytes()
    repeated = subprocess.run(
        command + ["interruption", "--job-sha256", manifest.job_sha256, "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 2 and output.read_bytes() == original
    summary = json.loads(captured.stdout)
    assert summary["promotion_allowed"] is False
    verify = command + [
        "verify-interruption",
        "--job-sha256",
        manifest.job_sha256,
        "--input",
        str(output),
        "--interruption-sha256",
        summary["interruption_sha256"],
    ]
    checked = subprocess.run(verify, capture_output=True, text=True)
    assert (
        checked.returncode == 0
        and json.loads(checked.stdout)["historical_observation_verified"] is True
    )
    output.chmod(0o644)
    output.write_bytes(output.read_bytes() + b" ")
    changed = subprocess.run(verify, capture_output=True, text=True)
    assert changed.returncode == 2 and not changed.stdout
    assert not scripted[2]
