import base64
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from ai_quant_os.validation_package_store import ValidationPackageStore
from test_research_hypothesis_api import NOW, HEADERS
from test_validation_package_api import prepare


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "infra/strategy-worker/worker.py"


@pytest.fixture
def package(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        payload, _, _ = prepare(client)
        response = client.post("/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload)
        assert response.status_code == 200, response.text
    with ValidationPackageStore(tmp_path / "paper.validation-packages.sqlite3") as archive:
        return archive.get(response.json()["package_ref"])


@pytest.fixture
def worker():
    assert WORKER.is_file(), "Confined prefix worker implementation is required"
    spec = importlib.util.spec_from_file_location("trusted_prefix_worker", WORKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def wire(package):
    from ai_quant_os.candidate_invocations import prepare_invocation

    return prepare_invocation(package, 2).model_dump_json().encode()


def profile():
    return {
        "platform": "linux",
        "implementation": "cpython",
        "version": (3, 11, 17),
        "isolated": 1,
        "no_bytecode": 1,
        "uid": 65534,
        "gid": 65534,
        "groups": (65534,),
        "working_directory": "/scratch",
        "worker_path": "/opt/strategy/worker.py",
        "environment": {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/scratch/home",
            "TMPDIR": "/scratch",
        },
        "status": {
            "NoNewPrivs": 1,
            "Seccomp": 2,
            "CapInh": 0,
            "CapPrm": 0,
            "CapEff": 0,
            "CapBnd": 0,
            "CapAmb": 0,
        },
        "cgroup": {
            "memory.max": "536870912",
            "memory.swap.max": "0",
            "pids.max": "64",
            "cpu.max": "100000 100000",
        },
        "mounts": {
            "/": {"options": {"ro"}},
            "/job": {"options": {"ro"}},
            "/scratch": {
                "options": {"rw", "noexec", "nosuid", "nodev"},
                "filesystem": "tmpfs",
                "size": 67108864,
            },
        },
        "network_interfaces": ("lo",),
        "limits": {
            "cpu": (30, 30),
            "fsize": (1048576, 1048576),
            "nofile": (128, 128),
            "nproc": (64, 64),
        },
        "job_files": ("request.json",),
        "scratch_files": (),
    }


def test_stdlib_worker_decodes_exact_parent_wire_without_loading_candidate(worker, package):
    from ai_quant_os.candidate_invocations import prepare_invocation

    raw = wire(package)
    request = worker.decode_request(raw)
    expected = prepare_invocation(package, 2)
    assert request.invocation_ref == expected.invocation_ref
    assert request.source == expected.source.text.encode()
    assert request.sdk_files == tuple(
        (row.path, base64.b64decode(row.raw_base64)) for row in expected.sdk_files
    )
    assert [row.observed_at for row in request.history] == [
        row.observed_at for row in expected.history
    ]
    assert [row.close for row in request.history] == [row.close for row in expected.history]


@pytest.mark.parametrize(
    "change",
    [
        "future",
        "history_hash",
        "reverse",
        "source_hash",
        "sdk_hash",
        "sdk_inventory",
        "duplicate_key",
        "extra",
        "authority",
        "nonfinite",
    ],
)
def test_worker_refuses_changed_or_unbounded_input_before_source_materialization(
    worker, package, change
):
    payload = json.loads(wire(package))
    if change == "future":
        payload["history"][-1]["observed_at"] = payload["execute_at"]
    elif change == "history_hash":
        payload["history_sha256"] = "0" * 64
    elif change == "reverse":
        payload["history"].reverse()
    elif change == "source_hash":
        payload["source"]["sha256"] = "0" * 64
    elif change == "sdk_hash":
        payload["sdk_files"][0]["sha256"] = "0" * 64
    elif change == "sdk_inventory":
        payload["sdk_files"].reverse()
    elif change == "extra":
        payload["full_holdout"] = "later rows"
    elif change == "authority":
        payload["isolation_verified"] = True
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    if change == "duplicate_key":
        raw = raw.replace(b'"index":2', b'"index":3,"index":2')
    if change == "nonfinite":
        raw = raw.replace(b'"index":2', b'"index":NaN')
    with pytest.raises(ValueError):
        worker.decode_request(raw)


def test_source_text_remains_inert_even_when_it_contains_a_host_side_effect(
    worker, package, tmp_path
):
    payload = json.loads(wire(package))
    marker = tmp_path / "must-not-exist.txt"
    text = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    payload["source"].update(text=text, sha256=sha256(text.encode()).hexdigest())
    request = worker.decode_request(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    )
    assert request.source == text.encode()
    assert not marker.exists()
    # Wire consistency is not original-package binding, trust or execution authority.


@pytest.mark.parametrize(
    "change",
    [
        "root_user",
        "group",
        "version",
        "flags",
        "env",
        "seccomp",
        "capability",
        "privileges",
        "memory",
        "swap",
        "pids",
        "cpu",
        "root_write",
        "job_write",
        "scratch_exec",
        "scratch_size",
        "network",
        "limits",
        "extra_job",
        "path",
    ],
)
def test_runtime_guard_refuses_weaker_or_foreign_profiles(worker, change):
    evidence = deepcopy(profile())
    if change == "root_user":
        evidence["uid"] = 0
    elif change == "group":
        evidence["groups"] = (0, 65534)
    elif change == "version":
        evidence["version"] = (3, 11, 9)
    elif change == "flags":
        evidence["isolated"] = 0
    elif change == "env":
        evidence["environment"]["SECRET"] = "must-not-be-present"
    elif change == "seccomp":
        evidence["status"]["Seccomp"] = 0
    elif change == "capability":
        evidence["status"]["CapEff"] = 1
    elif change == "privileges":
        evidence["status"]["NoNewPrivs"] = 0
    elif change in ("memory", "swap", "pids", "cpu"):
        key = {
            "memory": "memory.max",
            "swap": "memory.swap.max",
            "pids": "pids.max",
            "cpu": "cpu.max",
        }[change]
        evidence["cgroup"][key] = "max"
    elif change == "root_write":
        evidence["mounts"]["/"]["options"] = {"rw"}
    elif change == "job_write":
        evidence["mounts"]["/job"]["options"] = {"rw"}
    elif change == "scratch_exec":
        evidence["mounts"]["/scratch"]["options"].remove("noexec")
    elif change == "scratch_size":
        evidence["mounts"]["/scratch"]["size"] *= 2
    elif change == "network":
        evidence["network_interfaces"] = ("lo", "eth0")
    elif change == "limits":
        evidence["limits"]["cpu"] = (-1, -1)
    elif change == "extra_job":
        evidence["job_files"] = ("request.json", "holdout.csv")
    else:
        evidence["worker_path"] = "/job/unowned-worker.py"
    with pytest.raises(ValueError):
        worker.validate_runtime_profile(evidence)


def test_matching_runtime_metadata_is_only_a_guard_check_not_a_certificate(worker):
    assert worker.validate_runtime_profile(profile()) is None


@pytest.mark.parametrize(
    "change", ["missing", "extra", "status", "mounts", "scratch", "dirty_scratch", "boolean"]
)
def test_malformed_runtime_evidence_is_refused_with_a_validation_error(worker, change):
    evidence = profile()
    if change == "missing":
        del evidence["environment"]
    elif change == "extra":
        evidence["skip_checks"] = True
    elif change == "status":
        evidence["status"] = None
    elif change == "mounts":
        evidence["mounts"] = []
    elif change == "scratch":
        del evidence["mounts"]["/scratch"]
    elif change == "dirty_scratch":
        evidence["scratch_files"] = ("future.csv",)
    else:
        evidence["isolated"] = True
    with pytest.raises(ValueError):
        worker.validate_runtime_profile(evidence)


@pytest.mark.parametrize("target", [-1, 0, 1])
def test_worker_output_matches_existing_unattested_response_contract(worker, target):
    from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput

    reference = "candidate-invocation:" + "a" * 64
    output = CandidateTargetOutput.from_wire(worker.encode_target(reference, target))
    assert output.target == target and output.invocation_ref == reference
    assert not output.isolation_verified and not output.promotion_authorized


@pytest.mark.parametrize("target", [True, 2, -2, 1.0, "1"])
def test_worker_output_rejects_boolean_non_integer_and_non_unit_targets(worker, target):
    with pytest.raises(ValueError):
        worker.encode_target("candidate-invocation:" + "a" * 64, target)


def test_real_worker_cli_refuses_host_runtime_before_any_candidate_execution(worker, tmp_path):
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(WORKER)], capture_output=True, timeout=15
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"PREFIX_WORKER_REFUSED" in completed.stderr
    assert not list(tmp_path.rglob("candidate.py"))
