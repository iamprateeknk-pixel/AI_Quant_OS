"""Collect completion metadata and replay numeric targets with the trusted native adapter."""

from decimal import Decimal
from typing import Literal

from ai_quant_os.candidate_invocations import assemble_unattested_tape
from ai_quant_os.signal_tape_replay import replay_signal_tape, verify_signal_replay
from ai_quant_os.strategy_execution_tape_contracts import (
    StrategyExecutionReplayReport,
    StrategyExecutionTape,
)
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_execution_contracts import StrategyPrefixExecutionReceipt
from ai_quant_os.validation_package_contracts import ValidationPackage


def assemble_strategy_execution_tape(
    package: ValidationPackage,
    receipts: tuple[StrategyPrefixExecutionReceipt, ...],
    *,
    image_id: str,
    worker_sha256: str,
) -> StrategyExecutionTape:
    require_digest(image_id)
    require_digest(worker_sha256, prefix="")
    if type(receipts) is not tuple or len(receipts) > 4094:
        raise ValueError("Bounded immutable completion inventory required")
    checked = tuple(
        StrategyPrefixExecutionReceipt.model_validate_json(row.model_dump_json())
        for row in receipts
    )
    tape = assemble_unattested_tape(package, tuple(row.target for row in checked))
    result = StrategyExecutionTape(
        package_ref=tape.package_ref,
        candidate_ref=tape.candidate_ref,
        image_id=image_id,
        worker_sha256=worker_sha256,
        receipts=tuple(sorted(checked, key=lambda row: row.target.invocation_ref)),
        tape=tape,
    )
    result.require_package(package)
    return result


def replay_strategy_execution_tape(
    package: ValidationPackage,
    collection: StrategyExecutionTape,
    *,
    quantity: int = 1,
    initial_balance: Decimal = Decimal("100000"),
    cost_scenario: Literal["BASE", "DECLARED_STRESS"] = "BASE",
) -> StrategyExecutionReplayReport:
    checked = StrategyExecutionTape.model_validate_json(collection.model_dump_json())
    checked.require_package(package)
    replay = replay_signal_tape(
        package,
        checked.tape,
        quantity=quantity,
        initial_balance=initial_balance,
        cost_scenario=cost_scenario,
    )
    result = StrategyExecutionReplayReport(collection=checked, replay=replay)
    verify_strategy_execution_replay(package, result)
    return result


def verify_strategy_execution_replay(
    package: ValidationPackage, report: StrategyExecutionReplayReport
) -> None:
    checked = StrategyExecutionReplayReport.model_validate_json(report.model_dump_json())
    checked.collection.require_package(package)
    verify_signal_replay(package, checked.replay)
