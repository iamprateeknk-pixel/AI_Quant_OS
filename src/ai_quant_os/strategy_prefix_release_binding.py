"""Reconstruct original inert prefix and recipe bindings from pinned recovery bytes."""

from pathlib import Path

from ai_quant_os.candidate_invocations import prepare_invocation
from ai_quant_os.strategy_container_lifecycle import StrategyContainerPlan, _attempt, _recipe
from ai_quant_os.strategy_image_content import encode_metadata, parse_metadata
from ai_quant_os.strategy_image_runtime import StrategyImageHandoff
from ai_quant_os.strategy_prefix_execution_contracts import strategy_start_request
from ai_quant_os.strategy_prefix_jobs import PreparedPrefixJob
from ai_quant_os.strategy_prefix_release_contracts import StrategyPrefixReleaseBundle
from ai_quant_os.validation_package_contracts import ValidationPackage


def require_release_package(
    bundle: StrategyPrefixReleaseBundle, package: ValidationPackage
) -> PreparedPrefixJob:
    checked = StrategyPrefixReleaseBundle.model_validate_json(bundle.model_dump_json())
    invocation = prepare_invocation(package, checked.index)
    files = {row.name: row for row in checked.files}
    raw = {name: row.content() for name, row in files.items()}
    ownership = checked.receipt.ownership
    parents = tuple((Path(row.path), row.identity) for row in checked.parents)
    job = PreparedPrefixJob(
        parents[-1][0] / checked.job_name,
        invocation.invocation_ref,
        checked.index,
        files["stage.json"].sha256,
        raw["request.json"],
        parents,
        checked.job_identity,
        files["request.json"].identity,
        files["stage.json"].identity,
    )
    if (
        checked.package_ref != package.package_ref
        or raw["request.json"] != invocation.model_dump_json().encode()
        or ownership.invocation_ref != invocation.invocation_ref
        or ownership.job_path != str(job.path)
        or ownership.stage_sha256 != job.stage_sha256
        or raw["stage.json"] != encode_metadata(job._payload(package))
        or raw["container.json"] != ownership.model_dump_json().encode()
        or raw["target.json"] != checked.receipt.target.model_dump_json().encode()
        or files["create.json"].sha256 != checked.receipt.create_sha256
    ):
        raise ValueError("Release archive differs from original package/job/completion")
    value = parse_metadata(raw["create.json"])
    try:
        plan = StrategyContainerPlan(
            ownership,
            StrategyImageHandoff.model_validate(value["handoff"]),
            tuple(value["environment"]),
            files["container.json"].sha256,
            files["create.json"].sha256,
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Release archive recipe is invalid") from error
    if (
        plan.handoff.loaded_image_id != ownership.image_id
        or plan.handoff.archive.worker_sha256 != ownership.worker_sha256
        or raw["create.json"] != _recipe(ownership, plan.handoff, plan.environment)
        or raw["create-attempt.json"] != _attempt(plan)
        or raw["start-attempt.json"]
        != strategy_start_request(ownership, plan.create_sha256, checked.receipt.exit.container_id)
    ):
        raise ValueError("Release archive differs from original create/start recipe")
    return job
