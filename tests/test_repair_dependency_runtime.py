"""Trusted dependency-only probe; no proposed source or datasets are supplied."""

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_quant_os.repair_containers import _create_arguments
from ai_quant_os.repair_sandbox import DockerConfinement, local_docker_command

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "infra/repair-worker/dependency_probe.py"
IMAGE = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"


def program():
    assert PROGRAM.is_file(), "Guarded Linux dependency probe is required"
    spec = importlib.util.spec_from_file_location("trusted_dependency_probe", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request():
    versions = re.findall(
        r"^([a-z0-9-]+)==([^\s]+)",
        (ROOT / "infra/repair-worker/requirements.lock").read_text(),
        re.MULTILINE,
    )
    return json.dumps(
        {
            "schema_version": "1.0",
            "purpose": "DEPENDENCY_RUNTIME_PROBE",
            "distributions": sorted(versions),
        },
        separators=(",", ":"),
    ).encode()


def test_probe_request_has_only_exact_distribution_metadata():
    assert len(program().decode_request(request())) == 51
    for field in ("candidate", "source", "sdk_files", "training", "holdout", "tests"):
        value = json.loads(request())
        value[field] = "forbidden"
        with pytest.raises(ValueError):
            program().decode_request(json.dumps(value).encode())


def test_duplicate_and_unbounded_probe_metadata_is_refused():
    for raw in (b'{"purpose":"a","purpose":"b"}', b"x" * 32769):
        with pytest.raises(ValueError):
            program().decode_request(raw)


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "path", "version", "boolean", "order", "purpose"]
)
def test_changed_distribution_inventory_is_rejected(change):
    value = json.loads(request())
    rows = value["distributions"]
    if change == "duplicate":
        rows[-1] = rows[0]
    elif change == "missing":
        rows.pop()
    elif change == "path":
        rows[0][0] = "../../candidate"
    elif change == "version":
        rows[0][1] = "exec(source)"
    elif change == "boolean":
        rows[0][0] = True
    elif change == "order":
        rows.reverse()
    else:
        value["purpose"] = "EXECUTE_CANDIDATE"
    with pytest.raises(ValueError):
        program().decode_request(json.dumps(value).encode())


def test_only_cpython_utf8_locale_coercion_is_admitted():
    module = program()
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/scratch/home",
        "TMPDIR": "/scratch",
    }
    module.require_environment(environment)
    module.require_environment({**environment, "LC_CTYPE": "C.UTF-8"})
    for invalid in (
        {**environment, "LC_CTYPE": "foreign"},
        {**environment, "LANG": "C.UTF-8"},
        {**environment, "PYTHONPATH": "/host"},
        {**environment, "HOME": "/host"},
    ):
        with pytest.raises(ValueError):
            module.require_environment(invalid)


@pytest.mark.skipif(sys.platform == "linux", reason="Host refusal check requires a non-Linux host")
def test_probe_refuses_host_before_dependency_import_or_scratch_writes():
    program()
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(PROGRAM)], capture_output=True, timeout=10
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == ("DEPENDENCY_PROBE_REFUSED" + os.linesep).encode()


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_DEPENDENCY_ENGINE_TEST") != "1",
    reason="Explicit real-engine dependency probe gate required",
)
def test_actual_linux_dependencies_and_trusted_pytest_runtime(tmp_path):
    program()
    raw = PROGRAM.read_bytes()
    policy = DockerConfinement(IMAGE, sha256(raw).hexdigest())
    assert policy.probe().available
    job = tmp_path / "job"
    job.mkdir()
    for name, content in (("verifier.py", raw), ("request.json", request())):
        (job / name).write_bytes(content)
        (job / name).chmod(0o444)
    owner = uuid4().hex
    args = _create_arguments(policy, SimpleNamespace(path=job), owner)
    intent = {
        "owner": owner,
        "image_id": IMAGE,
        "arguments": args,
        "suite_sha256": policy.suite_sha256,
        "request_sha256": sha256(request()).hexdigest(),
    }
    with (tmp_path / "create-intent.json").open("x") as stream:
        json.dump(intent, stream)
        stream.flush()
        os.fsync(stream.fileno())
    executable = shutil.which("docker")
    identity = "ai-quant-repair-" + owner
    try:
        created = local_docker_command(executable, *args)
        assert created.returncode == 0
        cid = created.stdout.decode("ascii").strip()
        assert re.fullmatch(r"[a-f0-9]{64}", cid)
        identity = cid
        inspected = local_docker_command(
            executable, "container", "inspect", cid, "--format", "{{json .}}"
        )
        assert inspected.returncode == 0
        metadata = json.loads(inspected.stdout)
        assert metadata["Id"] == cid and metadata["Name"] == "/ai-quant-repair-" + owner
        policy.require_container(metadata, owner=owner, job=job)
        (tmp_path / "created-inspect.json").write_bytes(inspected.stdout)
        assert (job / "verifier.py").read_bytes() == raw and (
            job / "request.json"
        ).read_bytes() == request()
        with (tmp_path / "start-intent.json").open("x") as stream:
            json.dump({"container_id": cid, "owner": owner, "timeout": 45}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        result = local_docker_command(executable, "container", "start", "--attach", cid, timeout=45)
        assert result.returncode == 0, "Trusted dependency probe refused; retain owned evidence"
        receipt = json.loads(result.stdout)
        assert receipt == {
            "schema_version": "1.0",
            "status": "DEPENDENCY_RUNTIME_OBSERVED",
            "python_version": "3.11.17",
            "pytest_version": "8.4.2",
            "distribution_count": 51,
            "trusted_tests_passed": 2,
            "readonly_write_denied": True,
            "network_route_denied": True,
            "proposed_source_executed": False,
            "promotion_allowed": False,
        }
        terminal = local_docker_command(
            executable, "container", "inspect", cid, "--format", "{{json .}}"
        )
        state = json.loads(terminal.stdout)["State"]
        assert terminal.returncode == 0 and state["Status"] == "exited" and state["ExitCode"] == 0
        assert all(
            state[key] is False for key in ("Running", "Paused", "Restarting", "OOMKilled", "Dead")
        )
        assert state["Error"] == "" and type(state["ExitCode"]) is int
        (tmp_path / "runtime-result.json").write_bytes(result.stdout)
        (tmp_path / "terminal-inspect.json").write_bytes(terminal.stdout)
    finally:
        inspected = local_docker_command(
            executable, "container", "inspect", identity, "--format", "{{json .}}"
        )
        assert inspected.returncode == 0, "Unknown create/cleanup outcome; original intent retained"
        metadata = json.loads(inspected.stdout)
        assert metadata["Name"] == "/ai-quant-repair-" + owner and metadata["Image"] == IMAGE
        assert re.fullmatch(r"[a-f0-9]{64}", metadata["Id"])
        if re.fullmatch(r"[a-f0-9]{64}", identity):
            assert metadata["Id"] == identity
        assert metadata["Config"]["Labels"]["ai-quant.repair-owner"] == owner
        removed = local_docker_command(
            executable, "container", "rm", "--force", "--volumes", metadata["Id"]
        )
        assert removed.returncode == 0 and removed.stdout.decode("ascii").strip() == metadata["Id"]
        (tmp_path / "removed.json").write_text(
            json.dumps({"owner": owner, "container_id": metadata["Id"], "state": "REMOVED"})
        )
