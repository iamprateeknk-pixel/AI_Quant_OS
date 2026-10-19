"""Owned repair-container admission, one-shot execution, and cleanup."""

import json
import os
import re
import shutil
from hashlib import sha256
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from ai_quant_os.repair_jobs import PreparedRepairJob
from ai_quant_os.repair_locks import repair_root_lock
from ai_quant_os.repair_removals import RemovalJournal
from ai_quant_os.repair_sandbox import (
    DockerConfinement,
    MEMORY_BYTES,
    SCRATCH_OPTIONS,
    VERIFIER_COMMAND,
    local_docker_command,
)
from ai_quant_os.repair_snapshots import canonical_json
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import parse_metadata


@dataclass(frozen=True)
class CreatedRepairContainer:
    container_id: str
    owner: str
    snapshot_sha256: str
    image_id: str

    @property
    def promotion_allowed(self) -> bool:
        return False


@dataclass(frozen=True)
class RepairExecutionReceipt:
    schema_version: Literal["1.0"]
    owner: str
    container_id: str
    image_id: str
    snapshot_sha256: str
    suite_sha256: str
    request_sha256: str
    state: Literal[
        "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION",
        "EXECUTION_FAILED_AWAITING_REVIEW",
    ]
    exit_code: int
    oom_killed: bool
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False


@dataclass(frozen=True)
class RepairExecutionRecovery:
    schema_version: Literal["1.0"]
    owner: str
    state: Literal["EXECUTION_OUTCOME_UNCONFIRMED", "EXECUTION_RECORDED"]
    retained_artifacts: tuple[str, ...]
    receipt: RepairExecutionReceipt | None


_EXECUTION_TIMEOUT = 45
_EXECUTION_OUTPUT_LIMIT = 65536


def _execution_paths(root, owner):
    if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{32}", owner):
        raise ValueError("Repair execution owner identity invalid")
    prefix = root / f"repair-execution-{owner}"
    return {
        "intent": prefix.with_suffix(".start-intent.json"),
        "stdout": prefix.with_suffix(".stdout"),
        "stderr": prefix.with_suffix(".stderr"),
        "result": prefix.with_suffix(".result.json"),
        "request": prefix.with_suffix(".request.json"),
    }


def read_repair_execution(root, owner: str) -> RepairExecutionRecovery:
    """Read retained execution evidence without contacting Docker or starting work."""
    paths = _execution_paths(root, owner)
    retained = tuple(
        name
        for key, name in (
            ("result", "result.json"),
            ("intent", "start-intent.json"),
            ("stderr", "stderr"),
            ("stdout", "stdout"),
            ("request", "request.json"),
        )
        if os.path.lexists(paths[key])
    )
    if "start-intent.json" not in retained:
        raise ValueError("Repair execution has no durable start intent")
    intent_raw = read_repair_image_input(paths["intent"], 8192)
    intent = parse_metadata(intent_raw)
    owner_id = intent.get("container_id")
    expected_intent = {
        "schema_version": "1.0",
        "owner": owner,
        "container_id": owner_id,
        "image_id": intent.get("image_id"),
        "snapshot_sha256": intent.get("snapshot_sha256"),
        "suite_sha256": intent.get("suite_sha256"),
        "request_sha256": intent.get("request_sha256"),
        "arguments": ["container", "start", "--attach", owner_id],
        "timeout_seconds": _EXECUTION_TIMEOUT,
        "output_limit": _EXECUTION_OUTPUT_LIMIT,
        "state": "START_REQUEST_MAY_HAVE_BEEN_SENT",
        "execution_verified": False,
        "promotion_allowed": False,
    }
    if (
        intent != expected_intent
        or type(owner_id) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", owner_id)
        or any(
            type(intent.get(name)) is not str or not re.fullmatch(r"[a-f0-9]{64}", intent[name])
            for name in ("snapshot_sha256", "suite_sha256", "request_sha256")
        )
        or type(intent.get("image_id")) is not str
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", intent["image_id"])
    ):
        raise ValueError("Repair execution start intent is invalid")

    if "request.json" in retained:
        request_raw = read_repair_image_input(paths["request"], 65536)
        if sha256(request_raw).hexdigest() != intent["request_sha256"]:
            raise ValueError("Retained repair request does not match execution intent")

    receipt = None
    if "result.json" in retained:
        if "stdout" not in retained or "stderr" not in retained:
            raise ValueError("Repair execution result is missing captured streams")
        result = parse_metadata(read_repair_image_input(paths["result"], 8192))
        expected_fields = set(RepairExecutionReceipt.__dataclass_fields__)
        if set(result) != expected_fields:
            raise ValueError("Repair execution receipt fields are incomplete or unknown")
        receipt = RepairExecutionReceipt(**result)
        stdout = read_repair_image_input(paths["stdout"], _EXECUTION_OUTPUT_LIMIT)
        stderr = read_repair_image_input(paths["stderr"], _EXECUTION_OUTPUT_LIMIT)
        if (
            receipt.schema_version != "1.0"
            or receipt.owner != owner
            or receipt.container_id != owner_id
            or receipt.image_id != intent["image_id"]
            or receipt.snapshot_sha256 != intent["snapshot_sha256"]
            or receipt.suite_sha256 != intent["suite_sha256"]
            or receipt.request_sha256 != intent["request_sha256"]
            or type(receipt.exit_code) is not int
            or not 0 <= receipt.exit_code <= 255
            or type(receipt.oom_killed) is not bool
            or receipt.state
            != (
                "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
                if receipt.exit_code == 0 and not receipt.oom_killed
                else "EXECUTION_FAILED_AWAITING_REVIEW"
            )
            or receipt.execution_verified is not False
            or receipt.promotion_allowed is not False
            or receipt.stdout_bytes != len(stdout)
            or receipt.stdout_sha256 != sha256(stdout).hexdigest()
            or receipt.stderr_bytes != len(stderr)
            or receipt.stderr_sha256 != sha256(stderr).hexdigest()
        ):
            raise ValueError("Repair execution receipt does not match retained evidence")
        for digest in (receipt.snapshot_sha256, receipt.suite_sha256, receipt.request_sha256):
            if type(digest) is not str or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Repair execution receipt digest is invalid")
    return RepairExecutionRecovery(
        schema_version="1.0",
        owner=owner,
        state="EXECUTION_RECORDED" if receipt is not None else "EXECUTION_OUTCOME_UNCONFIRMED",
        retained_artifacts=retained,
        receipt=receipt,
    )


def _persist_execution_file(path, raw, *, limit):
    if type(raw) is not bytes or len(raw) > limit:
        raise ValueError("Repair execution evidence exceeds its bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _terminal_execution(value, *, container_id):
    try:
        state = value["State"]
        exit_code = state["ExitCode"]
        restart_count = value["RestartCount"]
        if (
            value["Id"] != container_id
            or state["Status"] != "exited"
            or any(
                type(state[key]) is not bool or state[key]
                for key in ("Running", "Paused", "Restarting", "Dead")
            )
            or type(state["OOMKilled"]) is not bool
            or type(state["Pid"]) is not int
            or state["Pid"] != 0
            or type(exit_code) is not int
            or not 0 <= exit_code <= 255
            or state["Error"] != ""
            or type(restart_count) is not int
            or restart_count != 0
        ):
            raise ValueError("Repair worker terminal state is not coherent")
        return exit_code, state["OOMKilled"]
    except (KeyError, TypeError) as error:
        raise ValueError("Repair worker terminal inspection is incomplete") from error


def run_repair_verifier(
    policy: DockerConfinement,
    job: PreparedRepairJob,
    created: CreatedRepairContainer,
) -> RepairExecutionReceipt:
    """Start the pinned verifier once and retain bounded, explicitly unattested output.

    Call only inside ``prepared_repair_container``. An intent is fsynced before the
    start RPC; if transport or inspection is ambiguous, the same owner cannot start
    again. Docker logging stays disabled and the result never grants promotion.
    """
    if (
        type(policy) is not DockerConfinement
        or type(job) is not PreparedRepairJob
        or type(created) is not CreatedRepairContainer
        or policy.suite_sha256 != job.suite_sha256
        or created.snapshot_sha256 != job.snapshot_sha256
        or created.image_id != policy.image_id
        or created.owner not in job.pending_containers
        or job.container_image(created.owner) != policy.image_id
        or not re.fullmatch(r"[a-f0-9]{64}", created.container_id)
    ):
        raise ValueError("Repair execution requires its active exact container lease")
    paths = _execution_paths(job.path.parent, created.owner)
    if any(os.path.lexists(path) for path in paths.values()):
        raise ValueError("Existing repair execution intent prevents repeated start")
    job.verify()
    request_raw = read_repair_image_input(job.path / "request.json", 65536)
    if sha256(request_raw).hexdigest() != job.request_sha256:
        raise ValueError("Prepared request does not match repair job identity")
    _persist_execution_file(paths["request"], request_raw, limit=65536)
    job.verify_container_lease(created.owner)
    executable = shutil.which("docker")
    if executable is None or not policy.probe().available:
        raise ValueError("Pinned repair execution runtime unavailable")
    observed = _inspect(policy, executable, created.container_id)
    _require_owner(
        observed,
        owner=created.owner,
        identity=created.container_id,
        image=policy.image_id,
    )
    policy.require_container(observed, owner=created.owner, job=job.path)
    if type(observed.get("RestartCount")) is not int or observed["RestartCount"] != 0:
        raise ValueError("Repair worker has prior start history")

    start_arguments = ["container", "start", "--attach", created.container_id]
    intent = {
        "schema_version": "1.0",
        "owner": created.owner,
        "container_id": created.container_id,
        "image_id": policy.image_id,
        "snapshot_sha256": job.snapshot_sha256,
        "suite_sha256": job.suite_sha256,
        "request_sha256": job.request_sha256,
        "arguments": start_arguments,
        "timeout_seconds": _EXECUTION_TIMEOUT,
        "output_limit": _EXECUTION_OUTPUT_LIMIT,
        "state": "START_REQUEST_MAY_HAVE_BEEN_SENT",
        "execution_verified": False,
        "promotion_allowed": False,
    }
    _persist_execution_file(paths["intent"], canonical_json(intent).encode(), limit=8192)
    execution = local_docker_command(
        executable,
        *start_arguments,
        timeout=_EXECUTION_TIMEOUT,
    )
    _persist_execution_file(paths["stdout"], execution.stdout, limit=_EXECUTION_OUTPUT_LIMIT)
    _persist_execution_file(paths["stderr"], execution.stderr, limit=_EXECUTION_OUTPUT_LIMIT)
    terminal = _inspect(policy, executable, created.container_id)
    _require_owner(
        terminal,
        owner=created.owner,
        identity=created.container_id,
        image=policy.image_id,
    )
    exit_code, oom_killed = _terminal_execution(terminal, container_id=created.container_id)
    if execution.returncode != exit_code:
        raise ValueError("Attached worker exit differs from terminal inspection")
    state = (
        "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"
        if exit_code == 0 and not oom_killed
        else "EXECUTION_FAILED_AWAITING_REVIEW"
    )
    receipt = RepairExecutionReceipt(
        schema_version="1.0",
        owner=created.owner,
        container_id=created.container_id,
        image_id=policy.image_id,
        snapshot_sha256=job.snapshot_sha256,
        suite_sha256=job.suite_sha256,
        request_sha256=job.request_sha256,
        state=state,
        exit_code=exit_code,
        oom_killed=oom_killed,
        stdout_bytes=len(execution.stdout),
        stdout_sha256=sha256(execution.stdout).hexdigest(),
        stderr_bytes=len(execution.stderr),
        stderr_sha256=sha256(execution.stderr).hexdigest(),
    )
    _persist_execution_file(paths["result"], canonical_json(receipt.__dict__).encode(), limit=8192)
    return receipt


def _inspect(policy, executable, identity):
    result = policy._command(executable, "container", "inspect", identity, "--format", "{{json .}}")
    if result.returncode:
        raise ValueError("Repair container ownership inspection unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("Repair container ownership metadata invalid")
    return value


def _require_owner(value, *, owner, identity, image):
    try:
        container_id = value["Id"]
        if (
            not isinstance(container_id, str)
            or not re.fullmatch(r"[a-f0-9]{64}", container_id)
            or identity not in {container_id, "ai-quant-repair-" + owner}
            or value["Name"] != "/ai-quant-repair-" + owner
            or value["Config"]["Labels"]["ai-quant.repair-owner"] != owner
            or value["Image"] != image
        ):
            raise ValueError("Repair container ownership mismatch")
        return container_id
    except (KeyError, TypeError) as error:
        raise ValueError("Repair container ownership metadata invalid") from error


def _remove_owned(policy, executable, identity, owner, job) -> str:
    with RemovalJournal(job.path.parent) as journal:
        journal.require_create_request(
            job, owner, image_id=policy.image_id, arguments=_create_arguments(policy, job, owner)
        )
        confirmed = journal.confirmed(job, owner, image_id=policy.image_id)
        if confirmed is not None:
            return confirmed
        if executable is None:
            raise ValueError("Retained repair cleanup runtime unavailable")
        value = _inspect(policy, executable, identity)
        container_id = _require_owner(value, owner=owner, identity=identity, image=policy.image_id)
        journal.intent(job, owner, container_id, image_id=policy.image_id)
        removed = policy._command(
            executable, "container", "rm", "--force", "--volumes", container_id
        )
        if removed.returncode or removed.stdout.decode("ascii").strip() != container_id:
            raise ValueError("Repair container removal unconfirmed")
        journal.removed(job, owner, container_id)
        return container_id


def _create_arguments(policy, job, owner):
    source = str(job.path.resolve())
    if any(character in source for character in (",", "\n", "\r", '"')):
        raise ValueError("Repair job mount path cannot be represented safely")
    return (
        "container",
        "create",
        "--name",
        "ai-quant-repair-" + owner,
        "--label",
        "ai-quant.repair-owner=" + owner,
        "--pull",
        "never",
        "--read-only",
        "--user",
        "65534:65534",
        "--workdir",
        "/scratch",
        "--entrypoint",
        "/usr/bin/env",
        "--network",
        "none",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        str(MEMORY_BYTES),
        "--memory-swap",
        str(MEMORY_BYTES),
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--restart",
        "no",
        "--tmpfs",
        "/scratch:" + SCRATCH_OPTIONS,
        "--ulimit",
        "cpu=30:30",
        "--ulimit",
        "fsize=1048576:1048576",
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "nproc=64:64",
        "--mount",
        "type=bind,source=" + source + ",target=/job,readonly",
        policy.image_id,
        *VERIFIER_COMMAND,
    )


@contextmanager
def prepared_repair_container(policy: DockerConfinement, job: PreparedRepairJob):
    """Create/admit only. Never start a container or execute its source/program.

    On ambiguous creation, inspect only the prepublished unique name for cleanup.
    Absence/inspection failure cannot prove no late create; retain the job lease.
    Removal is always by exact inspected immutable ID after ownership validation.
    """
    if type(policy) is not DockerConfinement or type(job) is not PreparedRepairJob:
        raise ValueError("Repair container requires owned policy and prepared job")
    if policy.suite_sha256 != job.suite_sha256:
        raise ValueError("Repair container suite pin mismatch")
    job.verify()
    with RemovalJournal(job.path.parent) as journal:
        journal.require_capacity()
    owner = uuid4().hex
    arguments = _create_arguments(policy, job, owner)
    executable = shutil.which("docker")
    if executable is None or not policy.probe().available:
        raise ValueError("Repair container prerequisites unavailable")
    identity = "ai-quant-repair-" + owner
    job.hold_container(owner, image_id=policy.image_id)
    with RemovalJournal(job.path.parent) as journal:
        journal.create_intent(job, owner, image_id=policy.image_id, arguments=arguments)
    try:
        result = policy._command(executable, *arguments)
        if result.returncode:
            raise ValueError("Repair container creation failed")
        created_id = result.stdout.decode("ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", created_id):
            raise ValueError("Repair container creation identity invalid")
        with RemovalJournal(job.path.parent) as journal:
            journal.create_ack(job, owner, created_id, image_id=policy.image_id)
        value = _inspect(policy, executable, created_id)
        identity = _require_owner(value, owner=owner, identity=created_id, image=policy.image_id)
        policy.require_container(value, owner=owner, job=job.path)
        job.verify()
        yield CreatedRepairContainer(identity, owner, job.snapshot_sha256, policy.image_id)
    finally:
        try:
            job.verify_container_lease(owner)
            _remove_owned(policy, executable, identity, owner, job)
            job.release_container(owner)
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError("Repair container cleanup unconfirmed; job retained") from error


def cleanup_retained_repair_container(policy: DockerConfinement, job: PreparedRepairJob) -> dict:
    """Use exact owned removal or its persisted acknowledgement; unknown absence refuses."""
    if (
        type(policy) is not DockerConfinement
        or type(job) is not PreparedRepairJob
        or policy.suite_sha256 != job.suite_sha256
    ):
        raise ValueError("Retained repair cleanup pins invalid")
    with repair_root_lock(job.path.parent):
        return _cleanup_retained_repair_container(policy, job)


def _cleanup_retained_repair_container(policy, job):
    owners = job.pending_containers
    if len(owners) != 1 or job.container_image(owners[0]) != policy.image_id:
        raise ValueError("Retained repair cleanup ownership invalid")
    with RemovalJournal(job.path.parent) as journal:
        confirmed = journal.confirmed(job, owners[0], image_id=policy.image_id)
    if confirmed is None:
        job.verify()
        job.verify_container_lease(owners[0])
    else:
        job.verify_remaining()
        receipt, raw = job._lease(owners[0], policy.image_id)
        try:
            metadata = receipt.lstat()
        except FileNotFoundError:
            pass
        else:
            job._verify_file(receipt, metadata, raw)
    executable = shutil.which("docker")
    owner = owners[0]
    container_id = _remove_owned(policy, executable, "ai-quant-repair-" + owner, owner, job)
    job.release_container(owner)
    job.cleanup()
    return {
        "schema_version": "1.0",
        "owner": owner,
        "container_id": container_id,
        "snapshot_sha256": job.snapshot_sha256,
        "state": "REMOVED_AFTER_RESTART_RECONCILIATION",
        "execution_verified": False,
        "promotion_allowed": False,
    }
