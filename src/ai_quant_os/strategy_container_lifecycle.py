"""Owned strategy container custody, fixed start transport and removal."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_sandbox import local_docker_command
from ai_quant_os.strategy_container_contracts import (
    StrategyContainerOwnership,
    StrategyContainerRecord,
)
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
from ai_quant_os.strategy_prefix_execution_contracts import strategy_start_request
from ai_quant_os.candidate_dataset_views import CandidateDatasetInput


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
    job: PreparedPrefixJob,
    package: CandidateDatasetInput,
    *,
    create_sha256: str,
    ownership_sha256: str,
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
    job: PreparedPrefixJob, package: CandidateDatasetInput, *, archive: Path, **pins
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
    package: CandidateDatasetInput,
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
    plan: StrategyContainerPlan, job: PreparedPrefixJob, package: CandidateDatasetInput
):
    """Inspect exact ownership and remove by immutable ID; unknown absence refuses."""
    if type(job) is not PreparedPrefixJob or type(plan) is not StrategyContainerPlan:
        raise ValueError("Exact strategy plan and original prepared job required")
    with strategy_container_custody(plan, job, package) as custody:
        return custody.remove_owned()


@dataclass
class StrategyContainerCustody:
    """A process-local session, valid only inside the cooperative root context."""

    plan: StrategyContainerPlan
    job: PreparedPrefixJob
    package: CandidateDatasetInput
    journal: StrategyContainerJournal
    executable: str | None
    identity: str
    active: bool = True

    def _require_active(self):
        if not self.active:
            raise ValueError("Strategy custody context has closed")

    def current(self) -> StrategyContainerRecord:
        self._require_active()
        record = self.journal.current(self.plan.ownership)
        if record is None:
            raise ValueError("Strategy owned create intent unavailable")
        return record

    def inspect_owned(self) -> dict:
        self._require_active()
        if self.executable is None:
            raise ValueError("Strategy owned container runtime unavailable")
        value = _read(
            self.executable, "container", "inspect", self.identity, "--format", "{{json .}}"
        )
        require_strategy_container_owner(value, self.plan.ownership, self.identity)
        return value

    def verify_inputs(self) -> None:
        self._require_active()
        # This session already owns the kernel root lock. The public verify method
        # would acquire the same non-reentrant lock a second time.
        self.job._verify(self.package)
        _verify(self.plan, self.job)

    def start_attached(self):
        record = self.current()
        if record.state != "CREATE_ACK" or record.container_id != self.identity:
            raise ValueError("Strategy start requires its exact create acknowledgement")
        value = self.inspect_owned()
        require_created_strategy_container(value, self.plan.ownership, self.plan.environment)
        if type(value.get("RestartCount", 0)) is not int or value.get("RestartCount", 0) != 0:
            raise ValueError("Strategy container has prior restart history")
        self.verify_inputs()
        _publish(
            _sidecar(self.job, "start-attempt"),
            strategy_start_request(self.plan.ownership, self.plan.create_sha256, self.identity),
        )
        return local_docker_command(
            self.executable, "container", "start", "--attach", self.identity, timeout=45
        )

    def kill_if_running(self) -> None:
        value = self.inspect_owned()
        running = value["State"]["Running"]
        if type(running) is not bool:
            raise ValueError("Strategy running state type invalid")
        if not running:
            return
        result = local_docker_command(
            self.executable, "container", "kill", "--signal", "SIGKILL", value["Id"]
        )
        if result.returncode or result.stdout.decode("ascii").strip() != value["Id"]:
            raise RuntimeError("Strategy kill acknowledgement uncertain")

    def remove_owned(self) -> StrategyContainerRecord:
        record = self.current()
        if record.state == "REMOVED":
            return record
        self.executable = _executable(self.plan)
        value = self.inspect_owned()
        cid = value["Id"]
        self.journal.removal_intent(self.plan.ownership, cid)
        result = local_docker_command(
            self.executable, "container", "rm", "--force", "--volumes", cid
        )
        if result.returncode or result.stdout.decode("ascii").strip() != cid:
            raise RuntimeError("Strategy removal unconfirmed; retain ownership and inputs")
        return self.journal.acknowledge_removal(self.plan.ownership, cid)

    def terminate_and_remove(self) -> StrategyContainerRecord:
        try:
            self.kill_if_running()
        except (OSError, ValueError, RuntimeError):
            # Independently inspected force-removal is the stronger terminal
            # acknowledgement when a kill/inspection outcome is uncertain.
            return self.remove_owned()
        return self.remove_owned()


@contextmanager
def strategy_container_custody(
    plan: StrategyContainerPlan, job: PreparedPrefixJob, package: CandidateDatasetInput
):
    if type(job) is not PreparedPrefixJob or type(plan) is not StrategyContainerPlan:
        raise ValueError("Exact strategy plan and original prepared job required")
    with job.admitted(package):
        ownership = _verify(plan, job)
        if read_repair_image_input(_sidecar(job, "create-attempt"), 8192) != _attempt(plan):
            raise ValueError("Strategy create attempt custody changed")
        with StrategyContainerJournal(job.path.parent) as journal:
            record = journal.current(ownership)
            if record is None:
                raise ValueError("Strategy create intent unavailable")
            executable = None if record.state == "REMOVED" else _executable(plan)
            custody = StrategyContainerCustody(
                plan,
                job,
                package,
                journal,
                executable,
                record.container_id or ownership.container_name,
            )
            try:
                yield custody
            finally:
                custody.active = False
