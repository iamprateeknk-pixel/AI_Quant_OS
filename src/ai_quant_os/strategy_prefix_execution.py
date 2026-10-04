"""One owned attached start, bounded unattested output and acknowledged cleanup."""

from hashlib import sha256
import os
from pathlib import Path

from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_container_lifecycle import (
    StrategyContainerPlan,
    strategy_container_custody,
)
from ai_quant_os.strategy_image_content import parse_metadata, require_digest
from ai_quant_os.strategy_image_loading import inspect_strategy_worker_image
from ai_quant_os.strategy_prefix_execution_contracts import (
    StrategyContainerExit,
    StrategyPrefixExecutionReceipt,
    strategy_start_request,
)
from ai_quant_os.strategy_prefix_jobs import PreparedPrefixJob
from ai_quant_os.validation_package_contracts import ValidationPackage


def _path(job, kind):
    return job.path.parent / (job.path.name + "." + kind + ".json")


def _publish(path, raw):

    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def execute_strategy_prefix(
    plan: StrategyContainerPlan,
    job: PreparedPrefixJob,
    package: ValidationPackage,
    *,
    archive: Path,
    **pins,
) -> StrategyPrefixExecutionReceipt:
    if type(plan) is not StrategyContainerPlan or type(job) is not PreparedPrefixJob:
        raise ValueError("Exact owned strategy plan and prepared job required")
    fresh = inspect_strategy_worker_image(archive, **pins)
    if (
        fresh.archive != plan.handoff.archive
        or fresh.engine_id != plan.handoff.engine_id
        or fresh.image_metadata_sha256 != plan.handoff.image_metadata_sha256
    ):
        raise ValueError("Strategy execution image/engine/source admission changed")
    with strategy_container_custody(plan, job, package) as custody:
        if custody.current().state != "CREATE_ACK":
            raise ValueError("Strategy execution requires a fresh acknowledged create")
        try:
            result = custody.start_attached()
            if result.returncode or result.stderr:
                raise ValueError("Prefix attached process did not complete cleanly")
            target = CandidateTargetOutput.from_wire(result.stdout)
            if (
                target.invocation_ref != job.invocation_ref
                or result.stdout != target.model_dump_json().encode()
            ):
                raise ValueError("Prefix target differs from original invocation/canonical output")
            exit = StrategyContainerExit.from_inspection(custody.inspect_owned())
            custody.verify_inputs()
        finally:
            removal = custody.terminate_and_remove()
        custody.verify_inputs()
        start = read_repair_image_input(_path(job, "start-attempt"), 8192)
        if start != strategy_start_request(
            plan.ownership, plan.create_sha256, removal.container_id
        ):
            raise ValueError("Prefix start custody differs from exact request")
        receipt = StrategyPrefixExecutionReceipt(
            ownership=plan.ownership,
            create_sha256=plan.create_sha256,
            start_sha256=sha256(start).hexdigest(),
            output_sha256=sha256(result.stdout).hexdigest(),
            exit_metadata_sha256=exit.metadata_sha256,
            target=target,
            exit=exit,
            removal=removal,
        )
        _publish(_path(job, "target"), result.stdout)
        _publish(_path(job, "execution"), receipt.model_dump_json().encode())
        return receipt


def restore_strategy_prefix_execution(
    plan: StrategyContainerPlan,
    job: PreparedPrefixJob,
    package: ValidationPackage,
    *,
    receipt_sha256: str,
) -> StrategyPrefixExecutionReceipt:
    require_digest(receipt_sha256, prefix="")
    with strategy_container_custody(plan, job, package) as custody:
        raw = read_repair_image_input(_path(job, "execution"), 65536)
        if sha256(raw).hexdigest() != receipt_sha256:
            raise ValueError("Prefix execution differs from independent receipt pin")
        receipt = StrategyPrefixExecutionReceipt.model_validate(parse_metadata(raw))
        if (
            raw != receipt.model_dump_json().encode()
            or receipt.ownership != plan.ownership
            or receipt.create_sha256 != plan.create_sha256
            or custody.current() != receipt.removal
        ):
            raise ValueError("Prefix execution differs from original plan/audited removal")
        start = read_repair_image_input(_path(job, "start-attempt"), 8192)
        target = read_repair_image_input(_path(job, "target"), 2048)
        if (
            start
            != strategy_start_request(
                plan.ownership, plan.create_sha256, receipt.removal.container_id
            )
            or sha256(start).hexdigest() != receipt.start_sha256
            or target != receipt.target.model_dump_json().encode()
            or sha256(target).hexdigest() != receipt.output_sha256
        ):
            raise ValueError("Prefix execution start/output custody differs")
        return receipt
