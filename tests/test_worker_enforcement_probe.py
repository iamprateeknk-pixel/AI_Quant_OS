"""Controlled kernel probes run only inside the fixed Linux worker profile."""

import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_quant_os.repair_containers import _create_arguments, _require_owner
from ai_quant_os.repair_sandbox import DockerConfinement, local_docker_command

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "infra/repair-worker/enforcement_probe.py"


def program():
    assert PROGRAM.is_file(), "Actual adversarial worker enforcement probes required"
    spec = importlib.util.spec_from_file_location("trusted_enforcement_probe", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "scenario", ["PRIVILEGE_AND_NOEXEC", "MEMORY", "CPU", "PROCESSES", "FILES"]
)
def test_request_admits_only_fixed_controlled_probe_scenarios(scenario):
    raw = json.dumps(
        {"schema_version": "1.0", "purpose": "WORKER_ENFORCEMENT_PROBE", "scenario": scenario}
    ).encode()
    assert program().decode_request(raw) == scenario


@pytest.mark.parametrize("change", ["source", "data", "duplicate", "unbounded", "unknown"])
def test_no_proposed_code_data_or_configurable_limit_is_accepted(change):
    value = {"schema_version": "1.0", "purpose": "WORKER_ENFORCEMENT_PROBE", "scenario": "MEMORY"}
    if change == "source":
        value["source"] = "exec(arbitrary)"
    elif change == "data":
        value["holdout"] = "forbidden"
    elif change == "unknown":
        value["scenario"] = "RELAX_LIMITS"
    raw = json.dumps(value).encode()
    if change == "duplicate":
        raw = b'{"scenario":"CPU","scenario":"MEMORY"}'
    elif change == "unbounded":
        raw = b"x" * 1025
    with pytest.raises(ValueError):
        program().decode_request(raw)


@pytest.mark.skipif(sys.platform == "linux", reason="Non-Linux host refusal required")
def test_probe_refuses_host_before_any_privilege_resource_or_subprocess_attempt():
    program()
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(PROGRAM)], capture_output=True, timeout=10
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == ("WORKER_ENFORCEMENT_PROBE_REFUSED" + os.linesep).encode()


def persist(path, value):
    with path.open("xb") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        stream.flush()
        os.fsync(stream.fileno())


@pytest.mark.skipif(
    os.environ.get("AI_QUANT_ENFORCEMENT_ENGINE_TEST") != "1",
    reason="Explicit real-engine controlled enforcement gate required",
)
@pytest.mark.parametrize(
    "scenario", ["PRIVILEGE_AND_NOEXEC", "MEMORY", "CPU", "PROCESSES", "FILES"]
)
def test_actual_privilege_and_resource_enforcement(scenario, tmp_path):
    module = program()
    image = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"
    sources = {
        "verifier.py": PROGRAM.read_bytes(),
        "runtime_guard.py": (ROOT / "infra/repair-worker/dependency_probe.py").read_bytes(),
        "request.json": json.dumps(
            {"schema_version": "1.0", "purpose": "WORKER_ENFORCEMENT_PROBE", "scenario": scenario},
            separators=(",", ":"),
        ).encode(),
    }
    assert sha256(sources["runtime_guard.py"]).hexdigest() == module.GUARD_SHA256
    policy = DockerConfinement(image, sha256(sources["verifier.py"]).hexdigest())
    assert policy.probe().available
    job = tmp_path / "job"
    job.mkdir()
    for name, raw in sources.items():
        (job / name).write_bytes(raw)
        (job / name).chmod(0o444)
    owner = uuid4().hex
    arguments = _create_arguments(policy, SimpleNamespace(path=job), owner)
    persist(
        tmp_path / "create-intent.json",
        {
            "owner": owner,
            "image_id": image,
            "arguments": arguments,
            "sources": {name: sha256(raw).hexdigest() for name, raw in sources.items()},
        },
    )
    executable = shutil.which("docker")
    identity = "ai-quant-repair-" + owner
    acknowledged = None
    try:
        created = local_docker_command(executable, *arguments)
        assert created.returncode == 0
        cid = created.stdout.decode("ascii").strip()
        assert re.fullmatch(r"[a-f0-9]{64}", cid)
        identity = acknowledged = cid
        persist(
            tmp_path / "create-ack.json", {"owner": owner, "container_id": cid, "image_id": image}
        )
        inspected = local_docker_command(
            executable, "container", "inspect", cid, "--format", "{{json .}}"
        )
        assert inspected.returncode == 0
        metadata = json.loads(inspected.stdout)
        assert _require_owner(metadata, owner=owner, identity=cid, image=image) == cid
        policy.require_container(metadata, owner=owner, job=job)
        persist(tmp_path / "created-inspect.json", metadata)
        assert sorted(row.name for row in job.iterdir()) == sorted(sources)
        assert all((job / name).read_bytes() == raw for name, raw in sources.items())
        persist(
            tmp_path / "start-intent.json",
            {"owner": owner, "container_id": cid, "scenario": scenario, "wall_timeout_seconds": 45},
        )
        result = local_docker_command(executable, "container", "start", "--attach", cid, timeout=45)
        inspected = local_docker_command(
            executable, "container", "inspect", cid, "--format", "{{json .}}"
        )
        assert inspected.returncode == 0
        metadata = json.loads(inspected.stdout)
        assert _require_owner(metadata, owner=owner, identity=cid, image=image) == cid
        state = metadata["State"]
        assert state["Status"] == "exited" and type(state["Pid"]) is int and state["Pid"] == 0
        assert all(state[key] is False for key in ("Running", "Paused", "Restarting", "Dead"))
        assert state["Error"] == "" and metadata["RestartCount"] == 0
        persist(tmp_path / "terminal-inspect.json", metadata)
        if scenario in ("MEMORY", "CPU"):
            assert result.returncode == state["ExitCode"] == 137
            assert state["OOMKilled"] is (scenario == "MEMORY")
            assert result.stdout == result.stderr == b""
            observation = {
                "scenario": scenario,
                "exit_code": 137,
                "oom_killed": state["OOMKilled"],
                "status": "CONTROLLED_LIMIT_KILL_OBSERVED",
            }
        else:
            assert result.returncode == state["ExitCode"] == 0 and state["OOMKilled"] is False
            assert result.stderr == b""
            observation = json.loads(result.stdout)
            assert (
                observation["scenario"] == scenario
                and observation["status"] == "CONTROLLED_ENFORCEMENT_OBSERVED"
            )
            assert all(
                observation[key] is False
                for key in ("proposed_source_executed", "isolation_verified", "promotion_allowed")
            )
            if scenario == "PRIVILEGE_AND_NOEXEC":
                assert observation["observations"] == {
                    "setuid_denied": True,
                    "setgid_denied": True,
                    "supplementary_groups_denied": True,
                    "raw_socket_denied": True,
                    "scratch_direct_execution_denied": True,
                }
            elif scenario == "PROCESSES":
                assert observation["observations"]["process_creation_denied"] is True
                assert 1 <= observation["observations"]["created_children"] <= 63
                assert observation["observations"]["observed_pids"] <= 64
            else:
                assert observation["observations"]["file_size_denied"] is True
                assert observation["observations"]["file_bytes"] == 1048576
                assert observation["observations"]["descriptor_limit_denied"] is True
                assert 1 <= observation["observations"]["opened_descriptors"] <= 125
        persist(tmp_path / "observations.json", observation)
        assert all((job / name).read_bytes() == raw for name, raw in sources.items())
    finally:
        inspected = local_docker_command(
            executable, "container", "inspect", identity, "--format", "{{json .}}"
        )
        assert inspected.returncode == 0, "Unknown container outcome; retain original intent"
        metadata = json.loads(inspected.stdout)
        cid = _require_owner(metadata, owner=owner, identity=identity, image=image)
        assert acknowledged is None or cid == acknowledged
        persist(
            tmp_path / "remove-intent.json",
            {"owner": owner, "container_id": cid, "image_id": image},
        )
        removed = local_docker_command(executable, "container", "rm", "--force", "--volumes", cid)
        assert removed.returncode == 0 and removed.stdout.decode("ascii").strip() == cid
        persist(
            tmp_path / "removed.json",
            {"owner": owner, "container_id": cid, "image_id": image, "state": "REMOVED"},
        )
