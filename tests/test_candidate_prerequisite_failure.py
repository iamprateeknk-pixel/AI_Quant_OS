"""Failed source-only prerequisites retain observations without trading statistics."""

from hashlib import sha256
import importlib
import json
import subprocess
import sys

import pytest

from test_strategy_prefix_worker import package as package
from test_candidate_dataset_views import unattested_test_metadata


def failure_module():
    assert importlib.util.find_spec("ai_quant_os.candidate_prerequisite_failure"), (
        "Source-bound prerequisite failure records required"
    )
    return importlib.import_module("ai_quant_os.candidate_prerequisite_failure")


@pytest.mark.parametrize("status", ["COMPILE_FAILED", "PROPOSED_TESTS_FAILED_UNATTESTED"])
def test_failure_retains_original_observation_without_synthetic_statistics(package, status):
    module = failure_module()
    update = (
        {"compiled_files": 2, "collected": 0, "passed": 0, "failed": 0}
        if status == "COMPILE_FAILED"
        else {"passed": 5, "failed": 1}
    )
    tested = unattested_test_metadata(package).model_copy(update={"status": status, **update})
    failure = module.record_prerequisite_failure(package, tested)
    assert failure.package_ref == package.package_ref
    assert failure.candidate_ref == package.candidate.candidate_ref
    assert failure.test_result == tested
    assert failure.test_result_sha256 == sha256(tested.model_dump_json().encode()).hexdigest()
    assert failure.outcome == "PREREQUISITE_FAILED"
    assert failure.reason == status
    assert failure.unexecuted_gates == (
        "OUT_OF_SAMPLE",
        "WALK_FORWARD",
        "STABILITY",
        "COST_STRESS",
        "MINIMUM_ACTIVITY",
        "MONTE_CARLO",
        "INDEPENDENT_HOLDOUT",
        "SOURCE_ATTESTATION",
        "ADVERSARIAL",
        "REALITY_GAP",
        "FINANCING",
        "ACCOUNT_CONVERSION",
        "INDEPENDENT_REVIEW",
        "SHADOW",
    )
    wire = json.loads(failure.model_dump_json())
    assert not {"holdout_net_pnl", "development_net_pnl", "monte_carlo", "fills"} & wire.keys()
    assert failure.isolation_verified is False and failure.promotion_allowed is False
    module.verify_prerequisite_failure(package, tested, failure)
    for altered in (
        tested.model_copy(update={"request_ref": "candidate-tests:" + "0" * 64}),
        tested.model_copy(update={"package_ref": "validation-package:" + "0" * 64}),
        tested.model_copy(update={"candidate_ref": "strategy-candidate:" + "0" * 64}),
        unattested_test_metadata(package),
    ):
        with pytest.raises(ValueError):
            module.record_prerequisite_failure(package, altered)
    for altered in (
        failure.model_copy(update={"reason": "OTHER"}),
        failure.model_copy(update={"test_result_sha256": "0" * 64}),
        failure.model_copy(update={"unexecuted_gates": failure.unexecuted_gates[:-1]}),
        failure.model_copy(update={"promotion_allowed": True}),
    ):
        with pytest.raises(ValueError):
            module.verify_prerequisite_failure(package, tested, altered)
    assert package.tests_status == "NOT_RUN" and package.validation_status == "NOT_RUN"


def test_failure_cli_requires_original_pinned_test_bytes_and_never_overwrites(package, tmp_path):
    module = failure_module()
    tested = unattested_test_metadata(package).model_copy(
        update={"status": "PROPOSED_TESTS_FAILED_UNATTESTED", "passed": 5, "failed": 1}
    )
    test_path = tmp_path / "failed-tests.json"
    raw = tested.model_dump_json().encode()
    test_path.write_bytes(raw)
    output = tmp_path / "failure.json"
    common = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_validation_cli",
        "--validation-archive",
        str(tmp_path / "paper.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--test-result",
        str(test_path),
        sha256(raw).hexdigest(),
    ]
    result = subprocess.run(
        [*common, "record-failure", "--output", str(output)], capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    expected = module.record_prerequisite_failure(package, tested)
    assert output.read_bytes() == expected.model_dump_json().encode()
    receipt = json.loads(result.stdout)
    assert receipt["failure_ref"] == expected.failure_ref
    retained = output.read_bytes()
    repeated = subprocess.run(
        [*common, "record-failure", "--output", str(output)], capture_output=True, timeout=30
    )
    assert repeated.returncode == 2 and output.read_bytes() == retained
    verified = subprocess.run(
        [*common, "verify-failure", "--failure", str(output), sha256(retained).hexdigest()],
        capture_output=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stderr
    test_path.write_bytes(raw + b" ")
    refused = subprocess.run(
        [*common, "record-failure", "--output", str(tmp_path / "refused.json")],
        capture_output=True,
        timeout=30,
    )
    assert refused.returncode == 2 and not (tmp_path / "refused.json").exists()
