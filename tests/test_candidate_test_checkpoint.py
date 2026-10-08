"""Source-test checkpoint observation cannot dispatch or reconcile a worker."""

from copy import deepcopy
from hashlib import sha256
import importlib
import json
import subprocess
import sys

import pytest

from ai_quant_os.candidate_test_custody import IMAGE_ID, persist_metadata, restore
from ai_quant_os.candidate_test_execution import _binding, _intent, _policy, prepare_candidate_tests
from test_candidate_validation_store import failed_tests
from test_repair_sandbox import container
from test_strategy_prefix_worker import package as package


CHECKPOINTS = (
    "NO_CREATE_INTENT_RETAINED",
    "CREATE_OUTCOME_UNCONFIRMED",
    "CREATE_ACK_RETAINED",
    "CREATED_ADMISSION_RETAINED",
    "START_OUTCOME_UNCONFIRMED",
    "TERMINAL_EXIT_RETAINED",
    "WORKER_OUTPUT_RETAINED",
    "REMOVAL_OUTCOME_UNCONFIRMED",
    "REMOVAL_ACK_RETAINED",
    "RESULT_RETAINED",
)

WITHOUT_NATIVE = """
import importlib.abc, runpy, sys
class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'nautilus_trader':
            raise ImportError('Native engine forbidden in checkpoint command')
sys.meta_path.insert(0, NoNative())
sys.argv = ['ai_quant_os.candidate_test_cli', *sys.argv[1:]]
runpy.run_module('ai_quant_os.candidate_test_cli', run_name='__main__')
"""


def module():
    assert importlib.util.find_spec("ai_quant_os.candidate_test_checkpoint"), (
        "Original source-test checkpoint diagnostics required"
    )
    return importlib.import_module("ai_quant_os.candidate_test_checkpoint")


@pytest.fixture
def retained(package, tmp_path, monkeypatch):
    root = tmp_path / "source-tests"
    root.mkdir()
    pin = prepare_candidate_tests(package, root)
    job = restore(root, pin)
    cid = "c" * 64
    binding = _binding(job, cid)
    created = container(job.path)
    created.update(Id=cid, Name="/ai-quant-repair-" + job.owner, Image=IMAGE_ID)
    created["Config"]["Labels"]["ai-quant.repair-owner"] = job.owner
    terminal = deepcopy(created)
    terminal.update(RestartCount=0)
    terminal["State"] = dict(
        Status="exited",
        ExitCode=0,
        Pid=0,
        Error="",
        Running=False,
        Paused=False,
        Restarting=False,
        OOMKilled=False,
        Dead=False,
    )
    result = failed_tests(package).model_dump(mode="json")
    records = (
        ("create-intent.json", _intent(job, _policy())),
        ("create-ack.json", binding),
        ("created-inspect.json", created),
        ("start-intent.json", {**binding, "timeout_seconds": 45}),
        ("terminal-inspect.json", terminal),
        ("worker-result.json", result),
        ("remove-intent.json", binding),
        ("removed.json", {**binding, "state": "REMOVED"}),
        ("result.json", result),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Checkpoint reader must never access a daemon or clean a worker")

    monkeypatch.setattr("ai_quant_os.candidate_test_execution.local_docker_command", forbidden)
    monkeypatch.setattr("ai_quant_os.repair_sandbox.DockerConfinement.probe", forbidden)
    monkeypatch.setattr("ai_quant_os.repair_sandbox.DockerConfinement._command", forbidden)
    return root, pin, job, records


def retain(retained, count):
    root, _, _, records = retained
    for name, value in records[:count]:
        persist_metadata(root / name, value)


@pytest.mark.parametrize("count", range(10))
def test_original_retained_checkpoint_is_specific_without_failure_authority(retained, count):
    root, pin, job, records = retained
    retain(retained, count)
    report = module().capture_candidate_test_checkpoint(root, pin)
    assert report.checkpoint == CHECKPOINTS[count]
    assert report.manifest_sha256 == pin and report.owner == job.owner
    assert report.request_ref == job.request.request_ref
    assert report.package_ref == job.request.package_ref
    assert report.container_id == ("c" * 64 if count >= 2 else None)
    assert report.test_status == ("PROPOSED_TESTS_FAILED_UNATTESTED" if count >= 6 else None)
    assert {row.name for row in report.files} == {name for name, _ in records[:count]}
    assert all(
        getattr(report, name) is False
        for name in (
            "worker_failure_confirmed",
            "cleanup_verified",
            "isolation_verified",
            "promotion_allowed",
        )
    )
    assert (
        str(root) not in report.model_dump_json()
        and "compiled_files" not in report.model_dump_json()
    )
    module().verify_candidate_test_checkpoint(root, pin, report)


def test_historical_checkpoint_remains_verifiable_after_original_completion(retained):
    root, pin, _, records = retained
    retain(retained, 4)
    report = module().capture_candidate_test_checkpoint(root, pin)
    for name, value in records[4:]:
        persist_metadata(root / name, value)
    module().verify_candidate_test_checkpoint(root, pin, report)
    assert report.checkpoint == "START_OUTCOME_UNCONFIRMED"
    assert module().capture_candidate_test_checkpoint(root, pin).checkpoint == "RESULT_RETAINED"


@pytest.mark.parametrize("change", ["digest", "owner", "checkpoint", "authority", "statistics"])
def test_changed_report_withholds_original_observation(retained, change):
    root, pin, _, _ = retained
    retain(retained, 4)
    report = module().capture_candidate_test_checkpoint(root, pin)
    updates = {
        "digest": {
            "files": (report.files[0].model_copy(update={"sha256": "0" * 64}), *report.files[1:])
        },
        "owner": {"owner": "0" * 32},
        "checkpoint": {"checkpoint": "RESULT_RETAINED"},
        "authority": {"cleanup_verified": True},
        "statistics": {"net_pnl": "10"},
    }
    with pytest.raises(ValueError):
        module().verify_candidate_test_checkpoint(
            root, pin, report.model_copy(update=updates[change])
        )


@pytest.mark.parametrize("change", ["foreign", "weakened", "missing_ack", "duplicate", "source"])
def test_changed_original_worker_evidence_withholds_checkpoint(retained, change):
    root, pin, _, _ = retained
    retain(retained, 4)
    if change == "missing_ack":
        (root / "create-ack.json").chmod(0o644)
        (root / "create-ack.json").unlink()
    elif change == "source":
        path = root / "job/request.json"
        path.chmod(0o644)
        path.write_bytes(b"{}")
    else:
        path = root / ("create-intent.json" if change == "duplicate" else "created-inspect.json")
        value = json.loads(path.read_bytes())
        if change == "foreign":
            value["Config"]["Labels"]["ai-quant.repair-owner"] = "foreign"
        elif change == "weakened":
            value["HostConfig"]["Privileged"] = True
        path.chmod(0o644)
        path.write_bytes(
            b'{"owner":"duplicate",' + path.read_bytes()[1:]
            if change == "duplicate"
            else json.dumps(value).encode()
        )
    with pytest.raises(ValueError):
        module().capture_candidate_test_checkpoint(root, pin)


def test_exclusive_checkpoint_export_and_independently_pinned_cli_verification(retained, tmp_path):
    root, pin, _, _ = retained
    retain(retained, 1)
    output = tmp_path / "checkpoint.json"
    common = [sys.executable, "-c", WITHOUT_NATIVE, "--root", str(root)]
    capture = common + ["checkpoint", "--manifest-sha256", pin, "--output", str(output)]
    child = subprocess.run(capture, capture_output=True, timeout=30)
    assert child.returncode == 0, child.stderr.decode()
    raw = output.read_bytes()
    assert json.loads(raw)["checkpoint"] == "CREATE_OUTCOME_UNCONFIRMED"
    verify = common + [
        "verify-checkpoint",
        "--manifest-sha256",
        pin,
        "--input",
        str(output),
        "--checkpoint-sha256",
        sha256(raw).hexdigest(),
    ]
    child = subprocess.run(verify, capture_output=True, timeout=30)
    assert (
        child.returncode == 0
        and json.loads(child.stdout)["historical_observation_verified"] is True
    )
    assert subprocess.run(capture, capture_output=True, timeout=30).returncode == 2
    output.chmod(0o644)
    output.write_bytes(raw + b" ")
    assert subprocess.run(verify, capture_output=True, timeout=30).returncode == 2


def test_checkpoint_export_cannot_pollute_original_worker_custody(retained):
    root, pin, _, _ = retained
    retain(retained, 1)
    output = root / "checkpoint.json"
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.candidate_test_cli",
            "--root",
            str(root),
            "checkpoint",
            "--manifest-sha256",
            pin,
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 2 and not output.exists()
    restore(root, pin)


def test_acknowledged_cleanup_does_not_invent_worker_start_or_result(retained):
    root, pin, _, records = retained
    retain(retained, 2)
    for name, value in records[6:8]:
        persist_metadata(root / name, value)
    report = module().capture_candidate_test_checkpoint(root, pin)
    assert report.checkpoint == "REMOVAL_ACK_RETAINED" and report.test_status is None
    assert "start-intent.json" not in {row.name for row in report.files}
    assert report.cleanup_verified is False and report.worker_failure_confirmed is False
    module().verify_candidate_test_checkpoint(root, pin, report)


@pytest.mark.parametrize("change", ["terminal", "removal", "worker_binding", "retained_result"])
def test_forged_terminal_removal_or_result_cannot_advance_checkpoint(retained, change):
    root, pin, _, _ = retained
    retain(retained, 9)
    name = {
        "terminal": "terminal-inspect.json",
        "removal": "removed.json",
        "worker_binding": "worker-result.json",
        "retained_result": "result.json",
    }[change]
    path = root / name
    value = json.loads(path.read_bytes())
    if change == "terminal":
        value["State"]["OOMKilled"] = True
    elif change == "removal":
        value["container_id"] = "d" * 64
    elif change == "worker_binding":
        value["request_ref"] = "candidate-tests:" + "0" * 64
    else:
        value.update(passed=4, failed=2)
    path.chmod(0o644)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module().capture_candidate_test_checkpoint(root, pin)


def test_deleted_historical_evidence_withholds_verification_even_if_remaining_state_is_valid(
    retained,
):
    root, pin, _, records = retained
    retain(retained, 4)
    report = module().capture_candidate_test_checkpoint(root, pin)
    for name, value in records[6:8]:
        persist_metadata(root / name, value)
    for name in ("start-intent.json", "created-inspect.json"):
        path = root / name
        path.chmod(0o644)
        path.unlink()
    assert (
        module().capture_candidate_test_checkpoint(root, pin).checkpoint == "REMOVAL_ACK_RETAINED"
    )
    with pytest.raises(ValueError):
        module().verify_candidate_test_checkpoint(root, pin, report)
