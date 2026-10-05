"""Trusted dependency-only Linux probe. Never accepts candidate code or datasets."""

import contextlib
import errno
import importlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys


class ProbeEnvironmentRefusal(ValueError):
    pass


def require_environment(environment):
    if (
        type(environment) is not dict
        or {key: value for key, value in environment.items() if key != "LC_CTYPE"}
        != {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch/home", "TMPDIR": "/scratch"}
        or environment.get("LC_CTYPE", "C.UTF-8") != "C.UTF-8"
    ):
        raise ProbeEnvironmentRefusal("Fixed probe environment changed")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate probe field")
        result[key] = value
    return result


def decode_request(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 32768:
        raise ValueError("Probe request bound")
    value = json.loads(raw, object_pairs_hook=_object)
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "purpose", "distributions"}
        or value["schema_version"] != "1.0"
        or value["purpose"] != "DEPENDENCY_RUNTIME_PROBE"
        or type(value["distributions"]) is not list
        or len(value["distributions"]) != 51
    ):
        raise ValueError("Only fixed dependency metadata is admitted")
    rows = value["distributions"]
    if (
        any(
            type(row) is not list
            or len(row) != 2
            or type(row[0]) is not str
            or not re.fullmatch(r"[a-z0-9-]{1,64}", row[0])
            or type(row[1]) is not str
            or not re.fullmatch(r"[a-zA-Z0-9.+!-]{1,64}", row[1])
            for row in rows
        )
        or rows != sorted(rows)
        or len({row[0] for row in rows}) != 51
    ):
        raise ValueError("Exact sorted distribution inventory required")
    return tuple(tuple(row) for row in rows)


def require_runtime():
    _require_runtime("/job/verifier.py", ["request.json", "verifier.py"])


def require_candidate_test_runtime():
    _require_runtime("/job/runtime_guard.py", ["request.json", "runtime_guard.py", "verifier.py"])


def _require_runtime(module_path, inventory):
    # Check host exclusion before importing Linux or third-party runtime modules.
    if sys.platform != "linux" or sys.implementation.name != "cpython":
        raise ValueError("Linux CPython worker required")
    import resource

    require_environment(dict(os.environ))
    if (
        tuple(sys.version_info[:3]) != (3, 11, 17)
        or sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or (os.geteuid(), os.getegid()) != (65534, 65534)
        or os.getgroups() not in ([], [65534])
        or os.getcwd() != "/scratch"
        or str(Path(__file__).resolve()) != module_path
        or sys.argv != ["/job/verifier.py", "/job/request.json"]
        or sorted(row.name for row in Path("/job").iterdir()) != inventory
        or list(Path("/scratch").iterdir())
        or sorted(row.name for row in Path("/sys/class/net").iterdir()) != ["lo"]
    ):
        raise ValueError("Fixed probe runtime changed")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    for key, expected in {
        "NoNewPrivs": 1,
        "Seccomp": 2,
        "CapInh": 0,
        "CapPrm": 0,
        "CapEff": 0,
        "CapBnd": 0,
        "CapAmb": 0,
    }.items():
        if int(status[key].strip(), 16 if key.startswith("Cap") else 10) != expected:
            raise ValueError("Probe privilege guard failed")
    for key, expected in {
        "memory.max": "536870912",
        "memory.swap.max": "0",
        "pids.max": "64",
        "cpu.max": "100000 100000",
    }.items():
        if " ".join((Path("/sys/fs/cgroup") / key).read_text().split()) != expected:
            raise ValueError("Probe cgroup guard failed")
    for name, limits in {
        "CPU": (30, 30),
        "FSIZE": (1048576, 1048576),
        "NOFILE": (128, 128),
        "NPROC": (64, 64),
    }.items():
        if resource.getrlimit(getattr(resource, "RLIMIT_" + name)) != limits:
            raise ValueError("Probe process limit failed")
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        fields = before.split()
        if fields[4] in ("/", "/job", "/scratch"):
            if fields[4] in mounts:
                raise ValueError("Duplicate guarded mount")
            mounts[fields[4]] = (set(fields[5].split(",")), after.split()[0])
    if (
        "ro" not in mounts["/"][0]
        or "ro" not in mounts["/job"][0]
        or not {"rw", "noexec", "nosuid", "nodev"} <= mounts["/scratch"][0]
        or mounts["/scratch"][1] != "tmpfs"
        or os.statvfs("/scratch").f_blocks * os.statvfs("/scratch").f_frsize != 67108864
    ):
        raise ValueError("Probe mount guard failed")


def _request():
    descriptor = os.open("/job/request.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 32768
        ):
            raise ValueError("Ordinary bounded probe request required")
        raw = stream.read(32769)
    if len(raw) != metadata.st_size:
        raise ValueError("Probe request changed during read")
    return decode_request(raw)


def probe():
    require_runtime()
    rows = _request()
    if any(version(name) != expected for name, expected in rows):
        raise ValueError("Installed distribution version changed")
    for module in (
        "fastapi",
        "pydantic",
        "pydantic_core",
        "uvicorn",
        "httpx",
        "langgraph.graph",
        "langgraph.checkpoint.sqlite",
        "pytest",
        "hypothesis",
    ):
        importlib.import_module(module)
    for path in ("/tmp/dependency-write-probe", "/job/dependency-write-probe"):
        try:
            with open(path, "xb"):
                pass
        except OSError as error:
            if error.errno != errno.EROFS:
                raise ValueError("Read-only enforcement not established") from error
        else:
            raise ValueError("Read-only write unexpectedly succeeded")
    with socket.socket() as connection:
        connection.settimeout(1)
        try:
            connection.connect(("192.0.2.1", 9))
        except OSError as error:
            if error.errno not in (errno.ENETUNREACH, errno.EHOSTUNREACH):
                raise ValueError("Absent network route not established") from error
        else:
            raise ValueError("Unexpected external route")
    tests = Path("/scratch/test_trusted_runtime.py")
    tests.write_text("""from pydantic import BaseModel
import sqlite3

def test_native_pydantic_model():
    class Number(BaseModel):
        value: int
    assert Number.model_validate({"value": "2"}).value == 2

def test_sqlite_runtime():
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("select 2+2").fetchone() == (4,)
""")
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import pytest

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        code = pytest.main(["-q", "-p", "no:cacheprovider", str(tests)])
    if code != 0 or "2 passed" not in captured.getvalue() or len(captured.getvalue()) > 8192:
        raise ValueError("Trusted runtime tests failed")
    return {
        "schema_version": "1.0",
        "status": "DEPENDENCY_RUNTIME_OBSERVED",
        "python_version": "3.11.17",
        "pytest_version": pytest.__version__,
        "distribution_count": len(rows),
        "trusted_tests_passed": 2,
        "readonly_write_denied": True,
        "network_route_denied": True,
        "proposed_source_executed": False,
        "promotion_allowed": False,
    }


def main():
    try:
        result = probe()
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        return 0
    except ProbeEnvironmentRefusal:
        # A fixed code diagnoses the trusted guard without echoing environment values.
        sys.stderr.write("DEPENDENCY_PROBE_REFUSED:ENVIRONMENT\n")
        return 2
    except Exception:
        sys.stderr.write("DEPENDENCY_PROBE_REFUSED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
