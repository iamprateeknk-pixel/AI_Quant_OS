"""Trusted worker admission is checked without executing candidate code on the host."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_strategy_prefix_worker import package as package
from ai_quant_os.candidate_test_jobs import candidate_test_request

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "infra/repair-worker/candidate_test_worker.py"


def worker():
    assert PROGRAM.is_file(), "Separate guarded candidate test worker is required"
    spec = importlib.util.spec_from_file_location("trusted_candidate_test_worker", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_decodes_exact_source_only_request(package):
    request = candidate_test_request(package)
    value, files = worker().decode_request(request.model_dump_json().encode())
    assert value["package_ref"] == package.package_ref
    assert sorted(files) == sorted(row.path for row in request.files)


def test_shared_runtime_guard_refuses_candidate_role_on_host():
    spec = importlib.util.spec_from_file_location(
        "shared_guard", ROOT / "infra/repair-worker/dependency_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "require_candidate_test_runtime"), (
        "Explicit fixed candidate role required"
    )
    if sys.platform != "linux":
        with pytest.raises(ValueError, match="Linux CPython"):
            module.require_candidate_test_runtime()


@pytest.mark.parametrize("change", ["data", "duplicate", "hash", "order", "authority", "oversize"])
def test_worker_rejects_changed_request(package, change):
    value = json.loads(candidate_test_request(package).model_dump_json())
    if change == "data":
        value["training"] = package.training.model_dump(mode="json")
    elif change == "duplicate":
        value["files"][-1] = value["files"][0]
    elif change == "hash":
        value["files"][0]["sha256"] = "0" * 64
    elif change == "order":
        value["files"].reverse()
    elif change == "authority":
        value["promotion_allowed"] = 0
    raw = json.dumps(value).encode() if change != "oversize" else b"x" * 524289
    with pytest.raises(ValueError):
        worker().decode_request(raw)


@pytest.mark.skipif(sys.platform == "linux", reason="Non-Linux host refusal check")
def test_worker_refuses_host_before_reading_request_or_executing_source():
    worker()
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(PROGRAM)], capture_output=True, timeout=10
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == ("CANDIDATE_TEST_WORKER_REFUSED" + os.linesep).encode()
