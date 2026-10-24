"""Checked candidate observations persist with immutable atomic audit binding."""

import importlib
from hashlib import sha256
import json
import sqlite3
import subprocess
import sys

import pytest

from test_strategy_prefix_worker import package as package
from test_strategy_prefix_run import scripted as scripted
from test_strategy_image_loading import assembled as assembled, inputs as inputs, daemon as daemon
from test_candidate_dataset_views import unattested_test_metadata
from test_candidate_validation import observed_reports


def store_type():
    assert importlib.util.find_spec("ai_quant_os.candidate_validation_store"), (
        "Checked candidate validation archive required"
    )
    return importlib.import_module(
        "ai_quant_os.candidate_validation_store"
    ).CandidateValidationStore


def failed_tests(package):
    return unattested_test_metadata(package).model_copy(
        update={"status": "PROPOSED_TESTS_FAILED_UNATTESTED", "passed": 5, "failed": 1}
    )


def test_checked_failures_survive_reopen_without_duplicate_audit(package, tmp_path):
    archive_type = store_type()
    path = tmp_path / "validation-results.sqlite3"
    with archive_type(path) as archive:
        failure = archive.put_failure(package, failed_tests(package))
        assert archive.put_failure(package, failed_tests(package)) == failure
        assert archive.get(failure.failure_ref) == failure
        with pytest.raises(ValueError):
            archive.put_failure(package, unattested_test_metadata(package))
    with archive_type(path) as archive:
        assert archive.get(failure.failure_ref) == failure
        assert archive.get("candidate-prerequisite-failure:" + "0" * 64) is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
        connection.execute("UPDATE candidate_validations SET artifact_json='{}'")
    with pytest.raises(ValueError):
        archive_type(path)


def test_archive_only_publishes_recomputed_source_bound_scorecards(package, scripted, tmp_path):
    archive_type = store_type()
    tested, holdout, windows = observed_reports(package, scripted)
    with archive_type(tmp_path / "results.sqlite3", capacity=1) as archive:
        score = archive.put_evaluation(package, tested, holdout, windows)
        assert archive.get(score.scorecard_ref) == score
        assert archive.put_evaluation(package, tested, holdout, windows) == score
        with pytest.raises(ValueError):
            archive.put_evaluation(package, tested, holdout, windows[::-1])
        with pytest.raises(ValueError):
            archive.put_failure(package, failed_tests(package))
        assert archive.get(score.scorecard_ref) == score


def test_archive_refuses_missing_audit_and_invalid_capacity(package, tmp_path):
    archive_type = store_type()
    for capacity in (0, 33, True):
        with pytest.raises(ValueError):
            archive_type(capacity=capacity)
    path = tmp_path / "missing-audit.sqlite3"
    with archive_type(path) as archive:
        archive.put_failure(package, failed_tests(package))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_head")
    with pytest.raises(ValueError):
        archive_type(path)


def test_failure_archive_cli_publishes_checked_record_for_fresh_readers(package, tmp_path):
    archive_type = store_type()
    tested = failed_tests(package)
    test_path = tmp_path / "failed-tests.json"
    raw = tested.model_dump_json().encode()
    test_path.write_bytes(raw)
    path = tmp_path / "candidate-results.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.candidate_validation_cli",
            "--validation-archive",
            str(tmp_path / "service.validation-packages.sqlite3"),
            "--package-ref",
            package.package_ref,
            "--test-result",
            str(test_path),
            sha256(raw).hexdigest(),
            "--record-archive",
            str(path),
            "archive-failure",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    with archive_type(path) as archive:
        retained = archive.get(receipt["failure_ref"])
    assert retained.test_result == tested
    assert retained.promotion_allowed is False
