"""Owned created-container admission and cleanup; candidate execution stays unavailable."""

import json
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from ai_quant_os.repair_jobs import PreparedRepairJob
from ai_quant_os.repair_sandbox import (
    DockerConfinement,
    MEMORY_BYTES,
    SCRATCH_OPTIONS,
    VERIFIER_COMMAND,
)


@dataclass(frozen=True)
class CreatedRepairContainer:
    container_id: str
    owner: str
    snapshot_sha256: str
    image_id: str

    @property
    def promotion_allowed(self) -> bool:
        return False


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


def _remove_owned(policy, executable, identity, owner) -> str:
    value = _inspect(policy, executable, identity)
    container_id = _require_owner(value, owner=owner, identity=identity, image=policy.image_id)
    removed = policy._command(executable, "container", "rm", "--force", "--volumes", container_id)
    if removed.returncode or removed.stdout.decode("ascii").strip() != container_id:
        raise ValueError("Repair container removal unconfirmed")
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
    owner = uuid4().hex
    arguments = _create_arguments(policy, job, owner)
    executable = shutil.which("docker")
    if executable is None or not policy.probe().available:
        raise ValueError("Repair container prerequisites unavailable")
    identity = "ai-quant-repair-" + owner
    job.hold_container(owner, image_id=policy.image_id)
    try:
        result = policy._command(executable, *arguments)
        if result.returncode:
            raise ValueError("Repair container creation failed")
        created_id = result.stdout.decode("ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", created_id):
            raise ValueError("Repair container creation identity invalid")
        value = _inspect(policy, executable, created_id)
        identity = _require_owner(value, owner=owner, identity=created_id, image=policy.image_id)
        policy.require_container(value, owner=owner, job=job.path)
        job.verify()
        yield CreatedRepairContainer(identity, owner, job.snapshot_sha256, policy.image_id)
    finally:
        try:
            job.verify_container_lease(owner)
            _remove_owned(policy, executable, identity, owner)
            job.release_container(owner)
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError("Repair container cleanup unconfirmed; job retained") from error


def cleanup_retained_repair_container(policy: DockerConfinement, job: PreparedRepairJob) -> dict:
    """Reconcile only a present, exactly owned container; absence remains unresolved."""
    if (
        type(policy) is not DockerConfinement
        or type(job) is not PreparedRepairJob
        or policy.suite_sha256 != job.suite_sha256
    ):
        raise ValueError("Retained repair cleanup pins invalid")
    owners = job.pending_containers
    if len(owners) != 1 or job.container_image(owners[0]) != policy.image_id:
        raise ValueError("Retained repair cleanup ownership invalid")
    job.verify()
    job.verify_container_lease(owners[0])
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Retained repair cleanup runtime unavailable")
    owner = owners[0]
    container_id = _remove_owned(policy, executable, "ai-quant-repair-" + owner, owner)
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
