"""Worker reports are checked data and never execution or promotion authority."""

import json
from pathlib import Path

import pytest

from ai_quant_os.candidate_test_contracts import CandidateTestResult


def value():
    return {
        "schema_version": "1.0",
        "request_ref": "candidate-tests:" + "1" * 64,
        "package_ref": "validation-package:" + "2" * 64,
        "candidate_ref": "strategy-candidate:" + "3" * 64,
        "python_version": "3.11.17",
        "pytest_version": "8.4.2",
        "compiled_files": 6,
        "collected": 6,
        "passed": 6,
        "failed": 0,
        "skipped": 0,
        "status": "PROPOSED_TESTS_PASSED_UNATTESTED",
        "isolation_verified": False,
        "execution_authorized": False,
        "promotion_allowed": False,
    }


def test_exact_proposed_test_result_is_only_an_unattested_observation():
    result = CandidateTestResult.model_validate_json(json.dumps(value()))
    assert result.passed == 6 and result.promotion_allowed is False


@pytest.mark.parametrize(
    "field,changed",
    [
        ("passed", 5),
        ("passed", True),
        ("failed", 1),
        ("skipped", 1),
        ("compiled_files", 5),
        ("python_version", "3.11.16"),
        ("pytest_version", "9.0.0"),
        ("promotion_allowed", True),
        ("promotion_allowed", 0),
        ("isolation_verified", True),
        ("execution_authorized", True),
        ("status", "VERIFIED"),
        ("training", "forbidden"),
    ],
)
def test_false_success_and_authority_are_refused(field, changed):
    report = value()
    report[field] = changed
    with pytest.raises(ValueError):
        CandidateTestResult.model_validate_json(json.dumps(report))


@pytest.mark.parametrize("evidence", [{}, {"State": None}, {"State": {"Status": "exited"}}])
def test_missing_terminal_metadata_is_an_explicit_refusal(evidence):
    from ai_quant_os.candidate_test_execution import _terminal

    with pytest.raises(ValueError):
        _terminal(evidence)


def test_published_candidate_test_schemas_match_models():
    from ai_quant_os.candidate_test_contracts import CandidateTestRequest

    root = Path(__file__).resolve().parents[1]
    for name, model in (
        ("candidate-test-request", CandidateTestRequest),
        ("candidate-test-result", CandidateTestResult),
    ):
        path = root / "docs/schemas" / (name + ".schema.json")
        assert path.is_file(), "Candidate test wire schema must be published"
        assert json.loads(path.read_bytes()) == model.model_json_schema()
