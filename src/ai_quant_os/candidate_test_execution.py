"""At-most-once Linux candidate tests with durable ownership and removal evidence."""

import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_test_custody import (
    IMAGE_ID,
    LOCK_NAME,
    WORKER_PIN,
    metadata,
    persist,
    persist_metadata,
    prepare,
    restore,
)
from ai_quant_os.repair_containers import _create_arguments, _inspect, _require_owner
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_sandbox import DockerConfinement, local_docker_command
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_prefix_jobs import _chain


def _root(path):
    path = Path(path).absolute()
    _chain(path)
    return path


def prepare_candidate_tests(package, root):
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        return prepare(package, root, uuid4().hex)


def _policy():
    return DockerConfinement(IMAGE_ID, WORKER_PIN)


def _intent(job, policy):
    return {
        "manifest_sha256": job.manifest_sha256,
        "owner": job.owner,
        "image_id": IMAGE_ID,
        "arguments": list(_create_arguments(policy, job, job.owner)),
        "request_ref": job.request.request_ref,
    }


def _binding(job, cid):
    return {
        "manifest_sha256": job.manifest_sha256,
        "owner": job.owner,
        "container_id": cid,
        "image_id": IMAGE_ID,
    }


def _require_intent(job, policy):
    if metadata(job.root / "create-intent.json") != _intent(job, policy):
        raise ValueError("Candidate test create intent changed")


def _ack(job):
    value = metadata(job.root / "create-ack.json")
    cid = value.get("container_id")
    if (
        type(cid) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", cid)
        or value != _binding(job, cid)
    ):
        raise ValueError("Candidate test create acknowledgement changed")
    return cid


def _terminal(value):
    try:
        _require_terminal(value)
    except (KeyError, TypeError) as error:
        raise ValueError("Candidate worker terminal metadata invalid") from error


def _require_terminal(value):
    state = value["State"]
    if (
        state["Status"] != "exited"
        or type(state["ExitCode"]) is not int
        or state["ExitCode"] != 0
        or type(state["Pid"]) is not int
        or state["Pid"] != 0
        or state["Error"] != ""
        or any(
            state[key] is not False
            for key in ("Running", "Paused", "Restarting", "OOMKilled", "Dead")
        )
        or type(value["RestartCount"]) is not int
        or value["RestartCount"] != 0
    ):
        raise ValueError("Candidate worker terminal state refused")


def _result(job, raw):
    # Parse duplicate-free JSON before model validation; source output has no authority.
    from ai_quant_os.strategy_image_content import parse_metadata

    value = parse_metadata(raw)
    result = CandidateTestResult.model_validate_json(json.dumps(value))
    if (result.request_ref, result.package_ref, result.candidate_ref) != (
        job.request.request_ref,
        job.request.package_ref,
        job.request.candidate_ref,
    ):
        raise ValueError("Candidate worker result request binding changed")
    return result


def _cleanup(job, policy, executable):
    _require_intent(job, policy)
    ack = job.root / "create-ack.json"
    cid = _ack(job) if ack.exists() else None
    if (job.root / "removed.json").exists():
        if cid is None or metadata(job.root / "removed.json") != {
            **_binding(job, cid),
            "state": "REMOVED",
        }:
            raise ValueError("Candidate test removal acknowledgement changed")
        if metadata(job.root / "remove-intent.json") != _binding(job, cid):
            raise ValueError("Candidate test removal intent changed")
        return cid
    if executable is None:
        raise ValueError("Candidate cleanup runtime unavailable; retain inputs")
    identity = cid or "ai-quant-repair-" + job.owner
    observed = _inspect(policy, executable, identity)
    confirmed = _require_owner(observed, owner=job.owner, identity=identity, image=IMAGE_ID)
    if cid is None:
        cid = confirmed
        persist_metadata(ack, _binding(job, cid))
    removal = job.root / "remove-intent.json"
    if removal.exists():
        if metadata(removal) != _binding(job, cid):
            raise ValueError("Candidate removal intent changed")
    else:
        persist_metadata(removal, _binding(job, cid))
    removed = policy._command(executable, "container", "rm", "--force", "--volumes", cid)
    if removed.returncode or removed.stdout.decode("ascii").strip() != cid:
        raise ValueError("Candidate test removal unconfirmed; retain inputs")
    persist_metadata(job.root / "removed.json", {**_binding(job, cid), "state": "REMOVED"})
    return cid


def cleanup_candidate_tests(root, manifest_sha256):
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        job = restore(root, manifest_sha256)
        return _cleanup(job, _policy(), shutil.which("docker"))


def _read_result(job):
    policy = _policy()
    _require_intent(job, policy)
    cid = _ack(job)
    if metadata(job.root / "start-intent.json") != {**_binding(job, cid), "timeout_seconds": 45}:
        raise ValueError("Candidate test start intent changed")
    created = metadata(job.root / "created-inspect.json")
    _require_owner(created, owner=job.owner, identity=cid, image=IMAGE_ID)
    policy.require_container(created, owner=job.owner, job=job.path)
    terminal = metadata(job.root / "terminal-inspect.json")
    _require_owner(terminal, owner=job.owner, identity=cid, image=IMAGE_ID)
    _terminal(terminal)
    _cleanup(job, policy, None)
    result = _result(job, read_repair_image_input(job.root / "worker-result.json", 65536))
    retained = CandidateTestResult.model_validate_json(
        read_repair_image_input(job.root / "result.json", 65536)
    )
    if result != retained:
        raise ValueError("Candidate retained result differs from worker evidence")
    return retained


def read_candidate_test_result(root, manifest_sha256):
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        return _read_result(restore(root, manifest_sha256))


def run_candidate_tests(root, manifest_sha256):
    root = _root(root)
    with kernel_root_lock(root, lock_name=LOCK_NAME):
        job = restore(root, manifest_sha256)
        if (root / "result.json").exists():
            return _read_result(job)
        if (root / "create-intent.json").exists():
            raise ValueError(
                "Existing candidate test intent prevents repeated execution; explicit cleanup required"
            )
        policy = _policy()
        executable = shutil.which("docker")
        if executable is None or not policy.probe().available:
            raise ValueError("Pinned Linux candidate test runtime unavailable")
        restore(root, manifest_sha256)
        persist_metadata(root / "create-intent.json", _intent(job, policy))
        try:
            created = policy._command(executable, *_create_arguments(policy, job, job.owner))
            if created.returncode:
                raise ValueError("Candidate test container creation failed")
            cid = created.stdout.decode("ascii").strip()
            if not re.fullmatch(r"[a-f0-9]{64}", cid):
                raise ValueError("Candidate test create identity invalid")
            persist_metadata(root / "create-ack.json", _binding(job, cid))
            observed = _inspect(policy, executable, cid)
            _require_owner(observed, owner=job.owner, identity=cid, image=IMAGE_ID)
            policy.require_container(observed, owner=job.owner, job=job.path)
            persist_metadata(root / "created-inspect.json", observed)
            restore(root, manifest_sha256)
            persist_metadata(
                root / "start-intent.json", {**_binding(job, cid), "timeout_seconds": 45}
            )
            execution = local_docker_command(
                executable, "container", "start", "--attach", cid, timeout=45
            )
            if execution.returncode:
                raise ValueError("Candidate test worker refused; retain owned evidence")
            observed = _inspect(policy, executable, cid)
            _require_owner(observed, owner=job.owner, identity=cid, image=IMAGE_ID)
            _terminal(observed)
            persist_metadata(root / "terminal-inspect.json", observed)
            result = _result(job, execution.stdout)
            persist(root / "worker-result.json", execution.stdout)
            restore(root, manifest_sha256)
        finally:
            # Unknown create/remove outcomes retain all job evidence and never retry start.
            restore(root, manifest_sha256)
            _cleanup(job, policy, executable)
        persist(root / "result.json", result.model_dump_json().encode())
        return _read_result(restore(root, manifest_sha256))
