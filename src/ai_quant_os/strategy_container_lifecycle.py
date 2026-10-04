"""Owned strategy create/admission/removal; never start or execute a worker."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_sandbox import local_docker_command
from ai_quant_os.strategy_container_contracts import StrategyContainerOwnership
from ai_quant_os.strategy_container_journal import (
    StrategyContainerJournal,
    reserve_strategy_container,
)
from ai_quant_os.strategy_container_policy import (
    strategy_create_arguments,
    require_created_strategy_container,
    require_strategy_container_owner,
)
from ai_quant_os.strategy_image_content import (
    encode_metadata,
    parse_metadata,
    read_strategy_image_base,
    require_digest,
)
from ai_quant_os.strategy_image_loading import inspect_strategy_worker_image
from ai_quant_os.strategy_image_runtime import StrategyImageHandoff, require_strategy_image_engine
from ai_quant_os.strategy_prefix_jobs import PreparedPrefixJob
from ai_quant_os.validation_package_contracts import ValidationPackage


@dataclass(frozen=True)
class StrategyContainerPlan:
    ownership: StrategyContainerOwnership
    handoff: StrategyImageHandoff
    environment: tuple[str, ...]
    ownership_sha256: str
    create_sha256: str

    @property
    def execution_verified(self):
        return False

    @property
    def promotion_allowed(self):
        return False


def _sidecar(job, kind):
    return job.path.parent / (job.path.name + "." + kind + ".json")


def _publish(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _recipe(ownership, handoff, environment):
    return encode_metadata(
        {
            "schema_version": "1.0",
            "ownership": ownership.model_dump(mode="json"),
            "handoff": handoff.model_dump(mode="json"),
            "environment": list(environment),
            "arguments": list(strategy_create_arguments(ownership)),
            "execution_verified": False,
            "promotion_allowed": False,
        }
    )


def _verify(plan, job):
    if type(plan) is not StrategyContainerPlan or type(job) is not PreparedPrefixJob:
        raise ValueError("Exact strategy plan and original prepared job required")
    ownership = StrategyContainerOwnership.model_validate_json(plan.ownership.model_dump_json())
    handoff = StrategyImageHandoff.model_validate_json(plan.handoff.model_dump_json())
    if (
        ownership.job_path != str(job.path)
        or ownership.job_name != job.path.name
        or ownership.invocation_ref != job.invocation_ref
        or ownership.stage_sha256 != job.stage_sha256
        or ownership.image_id != handoff.loaded_image_id
        or ownership.worker_sha256 != handoff.archive.worker_sha256
    ):
        raise ValueError("Strategy plan differs from original job/image/source")
    raw = read_repair_image_input(_sidecar(job, "container"), 8192)
    if (
        sha256(raw).hexdigest() != plan.ownership_sha256
        or raw != ownership.model_dump_json().encode()
    ):
        raise ValueError("Strategy ownership differs from independent plan")
    recipe = _recipe(ownership, handoff, plan.environment)
    if (
        sha256(recipe).hexdigest() != plan.create_sha256
        or read_repair_image_input(_sidecar(job, "create"), 65536) != recipe
    ):
        raise ValueError("Strategy create recipe differs from independent plan")
    return ownership


def restore_strategy_container_plan(
    job: PreparedPrefixJob, package: ValidationPackage, *, create_sha256: str, ownership_sha256: str
) -> StrategyContainerPlan:
    """Restore only an independently pinned exact recipe; never reissue creation."""
    require_digest(create_sha256, prefix="")
    require_digest(ownership_sha256, prefix="")
    if type(job) is not PreparedPrefixJob:
        raise ValueError("Original prepared strategy job required")
    with job.admitted(package):
        raw = read_repair_image_input(_sidecar(job, "create"), 65536)
        if sha256(raw).hexdigest() != create_sha256:
            raise ValueError("Strategy create recipe pin differs")
        value = parse_metadata(raw)
        try:
            plan = StrategyContainerPlan(
                StrategyContainerOwnership.model_validate(value["ownership"]),
                StrategyImageHandoff.model_validate(value["handoff"]),
                tuple(value["environment"]),
                ownership_sha256,
                create_sha256,
            )
        except (KeyError, TypeError) as error:
            raise ValueError("Strategy retained recipe metadata invalid") from error
        _verify(plan, job)
        with StrategyContainerJournal(job.path.parent) as journal:
            if journal.current(plan.ownership) is None:
                raise ValueError("Strategy retained recipe lacks durable ownership")
        return plan


def _attempt(plan):
    return encode_metadata(
        {
            "schema_version": "1.0",
            "owner": plan.ownership.owner,
            "create_sha256": plan.create_sha256,
            "state": "CREATE_REQUEST_MAY_HAVE_BEEN_SENT",
            "execution_verified": False,
            "promotion_allowed": False,
        }
    )


def _read(executable, *argv):
    result = local_docker_command(executable, *argv)
    if result.returncode:
        raise ValueError("Strategy daemon metadata unavailable")
    return parse_metadata(result.stdout)


def _executable(plan):
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Strategy local Docker unavailable")
    engine = _read(executable, "info", "--format", "{{json .}}")
    version = _read(executable, "version", "--format", "{{json .Server}}")
    require_strategy_image_engine(engine, version)
    if engine["ID"] != plan.handoff.engine_id:
        raise ValueError("Strategy engine identity changed")
    return executable


def prepare_strategy_container(
    job: PreparedPrefixJob, package: ValidationPackage, *, archive: Path, **pins
) -> StrategyContainerPlan:
    if type(job) is not PreparedPrefixJob:
        raise ValueError("Original prepared strategy job required")
    job.verify(package)
    handoff = inspect_strategy_worker_image(archive, **pins)
    environment = tuple(
        read_strategy_image_base(pins["base_metadata"], pins["base_manifest_digest"]).config[
            "config"
        ]["Env"]
    )
    ownership = reserve_strategy_container(
        job, package, image_id=handoff.loaded_image_id, worker_sha256=handoff.archive.worker_sha256
    )
    recipe = _recipe(ownership, handoff, environment)
    if len(recipe) > 65536:
        raise ValueError("Strategy create recipe exceeds bound")
    with job.admitted(package):
        _publish(_sidecar(job, "create"), recipe)
    return StrategyContainerPlan(
        ownership,
        handoff,
        environment,
        sha256(ownership.model_dump_json().encode()).hexdigest(),
        sha256(recipe).hexdigest(),
    )


def create_strategy_container(
    plan: StrategyContainerPlan,
    job: PreparedPrefixJob,
    package: ValidationPackage,
    *,
    archive: Path,
    **pins,
) -> str:
    """At most one create request; unknown outcomes retain the plan and all inputs."""
    if type(job) is not PreparedPrefixJob or type(plan) is not StrategyContainerPlan:
        raise ValueError("Exact strategy plan and original prepared job required")
    with job.admitted(package):
        ownership = _verify(plan, job)
        with StrategyContainerJournal(job.path.parent) as journal:
            current = journal.current(ownership)
            if current is None or current.state != "CREATE_INTENT":
                raise ValueError("Strategy create is no longer admissible")
            fresh = inspect_strategy_worker_image(archive, **pins)
            environment = tuple(
                read_strategy_image_base(
                    pins["base_metadata"], pins["base_manifest_digest"]
                ).config["config"]["Env"]
            )
            if (
                fresh.archive != plan.handoff.archive
                or fresh.engine_id != plan.handoff.engine_id
                or fresh.image_metadata_sha256 != plan.handoff.image_metadata_sha256
                or environment != plan.environment
            ):
                raise ValueError("Strategy image/engine/source admission changed")
            executable = _executable(plan)
            _publish(_sidecar(job, "create-attempt"), _attempt(plan))
            try:
                result = local_docker_command(executable, *strategy_create_arguments(ownership))
                if result.returncode:
                    raise ValueError("Strategy create not acknowledged")
                cid = result.stdout.decode("ascii").strip()
                if not re.fullmatch("[a-f0-9]{64}", cid):
                    raise ValueError("Strategy create identity invalid")
                journal.acknowledge_create(ownership, cid)
                value = _read(executable, "container", "inspect", cid, "--format", "{{json .}}")
                require_strategy_container_owner(value, ownership, cid)
                require_created_strategy_container(value, ownership, environment)
                # The admitted context rechecks exact inputs before returning.
                return cid
            except (OSError, ValueError, RuntimeError) as error:
                raise RuntimeError(
                    "Strategy create/admission uncertain; retain inputs and reconcile the unique owned name"
                ) from error


def cleanup_strategy_container(
    plan: StrategyContainerPlan, job: PreparedPrefixJob, package: ValidationPackage
):
    """Inspect exact ownership and remove by immutable ID; unknown absence refuses."""
    if type(job) is not PreparedPrefixJob or type(plan) is not StrategyContainerPlan:
        raise ValueError("Exact strategy plan and original prepared job required")
    with job.admitted(package):
        ownership = _verify(plan, job)
        if read_repair_image_input(_sidecar(job, "create-attempt"), 8192) != _attempt(plan):
            raise ValueError("Strategy create attempt custody changed")
        with StrategyContainerJournal(job.path.parent) as journal:
            current = journal.current(ownership)
            if current is None:
                raise ValueError("Strategy create intent unavailable")
            if current.state == "REMOVED":
                return current
            executable = _executable(plan)
            identity = current.container_id or ownership.container_name
            value = _read(executable, "container", "inspect", identity, "--format", "{{json .}}")
            cid = require_strategy_container_owner(value, ownership, identity)
            journal.removal_intent(ownership, cid)
            result = local_docker_command(
                executable, "container", "rm", "--force", "--volumes", cid
            )
            if result.returncode or result.stdout.decode("ascii").strip() != cid:
                raise RuntimeError("Strategy removal unconfirmed; retain ownership and inputs")
            return journal.acknowledge_removal(ownership, cid)
