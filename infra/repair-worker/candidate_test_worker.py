"""Run exact proposed source tests only after the fixed Linux guard succeeds."""

import base64
import contextlib
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

GUARD_SHA256 = "59ac6c56aeaf0c062d8f732b77978a11b6163d16fc326c0a3e8ad8014625aa85"
PATHS = (
    "ai_quant_os/__init__.py",
    "ai_quant_os/contracts.py",
    "ai_quant_os/market_data.py",
    "ai_quant_os/strategy_contracts.py",
    "candidate.py",
    "test_candidate.py",
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate candidate test field")
        result[key] = value
    return result


def decode_request(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 524288:
        raise ValueError("Candidate test request bound")
    value = json.loads(raw, object_pairs_hook=_object)
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "package_ref",
            "candidate_ref",
            "files",
            "status",
            "promotion_allowed",
        }
        or value["schema_version"] != "1.0"
        or value["status"] != "NO_DATA_CANDIDATE_TEST_REQUEST"
        or value["promotion_allowed"] is not False
        or type(value["package_ref"]) is not str
        or not re.fullmatch(r"validation-package:[a-f0-9]{64}", value["package_ref"])
        or type(value["candidate_ref"]) is not str
        or not re.fullmatch(r"strategy-candidate:[a-f0-9]{64}", value["candidate_ref"])
        or type(value["files"]) is not list
        or len(value["files"]) != 6
    ):
        raise ValueError("Source-only candidate test request required")
    files = {}
    for expected, row in zip(PATHS, value["files"], strict=True):
        if (
            type(row) is not dict
            or set(row) != {"path", "raw_base64", "sha256"}
            or row["path"] != expected
            or type(row["sha256"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
            or type(row["raw_base64"]) is not str
            or len(row["raw_base64"]) > 87384
        ):
            raise ValueError("Exact candidate test inventory required")
        content = base64.b64decode(row["raw_base64"], validate=True)
        maximum = 8192 if expected in ("candidate.py", "test_candidate.py") else 65536
        if (
            not 0 < len(content) <= maximum
            or base64.b64encode(content).decode() != row["raw_base64"]
            or sha256(content).hexdigest() != row["sha256"]
        ):
            raise ValueError("Candidate test source bytes changed")
        files[expected] = content
    return value, files


def _read(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("Bounded ordinary worker input required")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(raw) != before.st_size:
        raise ValueError("Worker input changed while reading")
    return raw


def _guard():
    if sys.platform != "linux" or sys.implementation.name != "cpython":
        raise ValueError("Linux worker required")
    raw = _read("/job/runtime_guard.py", 32768)
    if sha256(raw).hexdigest() != GUARD_SHA256:
        raise ValueError("Fixed runtime guard changed")
    spec = importlib.util.spec_from_file_location("trusted_runtime_guard", "/job/runtime_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.require_candidate_test_runtime()


class _Capture(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 8192:
            raise ValueError("Candidate test output exceeded bound")
        return super().write(value)


class _Results:
    def __init__(self):
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed += 1
        elif report.skipped:
            self.skipped += 1
        elif report.when == "call" and report.passed:
            self.passed += 1


def run():
    _guard()
    raw = _read("/job/request.json", 524288)
    value, files = decode_request(raw)
    result = {
        "schema_version": "1.0",
        "request_ref": "candidate-tests:" + sha256(raw).hexdigest(),
        "package_ref": value["package_ref"],
        "candidate_ref": value["candidate_ref"],
        "python_version": "3.11.17",
        "pytest_version": "8.4.2",
        "compiled_files": 0,
        "collected": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "status": "COMPILE_FAILED",
        "isolation_verified": False,
        "execution_authorized": False,
        "promotion_allowed": False,
    }
    runtime = Path("/scratch/runtime")
    runtime.mkdir()
    for name, content in files.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        try:
            compile(content, str(path), "exec", dont_inherit=True)
        except (SyntaxError, UnicodeError, ValueError):
            return result
        result["compiled_files"] += 1
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import pytest

    if pytest.__version__ != "8.4.2":
        raise ValueError("Fixed pytest version changed")
    sys.path.insert(0, str(runtime))
    counts = _Results()
    with contextlib.redirect_stdout(_Capture()), contextlib.redirect_stderr(_Capture()):
        code = pytest.main(
            [
                "-q",
                "--noconftest",
                "-p",
                "no:cacheprovider",
                "--basetemp=/scratch/pytest",
                str(runtime / "test_candidate.py"),
            ],
            plugins=[counts],
        )
    for key in ("collected", "passed", "failed", "skipped"):
        result[key] = getattr(counts, key)
    result["status"] = (
        "PROPOSED_TESTS_PASSED_UNATTESTED"
        if code == 0
        and (counts.collected, counts.passed, counts.failed, counts.skipped) == (6, 6, 0, 0)
        else "PROPOSED_TESTS_FAILED_UNATTESTED"
    )
    return result


def main():
    try:
        result = run()
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception:
        sys.stderr.write("CANDIDATE_TEST_WORKER_REFUSED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
