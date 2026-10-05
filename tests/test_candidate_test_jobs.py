"""Candidate test inputs stay inert on the parent and exclude all dataset payloads."""

import base64
from hashlib import sha256
import importlib
import json

import pytest

from test_strategy_prefix_worker import package as package


def module():
    assert importlib.util.find_spec("ai_quant_os.candidate_test_jobs"), (
        "Separate no-data candidate test jobs are required"
    )
    return importlib.import_module("ai_quant_os.candidate_test_jobs")


def test_request_retains_exact_source_tests_and_sdk_without_datasets(package):
    request = module().candidate_test_request(package)
    value = json.loads(request.model_dump_json())
    assert set(value) == {
        "schema_version",
        "package_ref",
        "candidate_ref",
        "files",
        "status",
        "promotion_allowed",
    }
    expected = {row.path: row.text.encode() for row in package.candidate.files}
    expected.update({row.path: base64.b64decode(row.raw_base64) for row in package.sdk_files})
    assert {row.path: base64.b64decode(row.raw_base64) for row in request.files} == expected
    assert len(request.files) == 6 and request.promotion_allowed is False
    assert package.training.raw_base64 not in request.model_dump_json()
    assert package.holdout.raw_base64 not in request.model_dump_json()
    assert (
        request.request_ref
        == "candidate-tests:" + sha256(request.model_dump_json().encode()).hexdigest()
    )


@pytest.mark.parametrize("change", ["data", "authority", "duplicate", "hash", "path"])
def test_request_rejects_extra_data_authority_and_changed_file_inventory(package, change):
    request = module().candidate_test_request(package)
    value = json.loads(request.model_dump_json())
    if change == "data":
        value["holdout"] = package.holdout.model_dump(mode="json")
    elif change == "authority":
        value["promotion_allowed"] = True
    elif change == "duplicate":
        value["files"][-1] = value["files"][0]
    elif change == "hash":
        value["files"][0]["sha256"] = "0" * 64
    else:
        value["files"][0]["path"] = "../../candidate.py"
    with pytest.raises(ValueError):
        type(request).model_validate(value)


@pytest.mark.parametrize("numeric", [0, 0.0])
def test_request_requires_literal_false_authority(package, numeric):
    request = module().candidate_test_request(package)
    value = json.loads(request.model_dump_json())
    value["promotion_allowed"] = numeric
    with pytest.raises(ValueError):
        type(request).model_validate(value)
