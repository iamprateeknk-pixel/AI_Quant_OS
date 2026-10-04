"""Prepare exact prefix-only JSON and bind numeric responses; never execute source."""

import base64
from decimal import Context, localcontext

from ai_quant_os.candidate_invocation_contracts import (
    CandidateInvocation,
    CandidateTargetOutput,
    ClosedBar,
)
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv
from ai_quant_os.signal_tape_contracts import SignalDecision, SignalTape
from ai_quant_os.validation_package_contracts import ValidationPackage


def _inputs(package: ValidationPackage):
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64, validate=True))
    return package, dataset


def _invocation(
    package: ValidationPackage,
    dataset: BarDataset,
    index: int,
    package_ref: str,
    candidate_ref: str,
) -> CandidateInvocation:
    if type(index) is not int or not 2 <= index < len(dataset.bars):
        raise ValueError("Invocation requires an existing non-warmup open index")
    history = dataset.bars[index - 2 : index]
    with localcontext(Context(prec=28)):
        fingerprint = BarDataset(history).fingerprint
    return CandidateInvocation(
        package_ref=package_ref,
        candidate_ref=candidate_ref,
        index=index,
        execute_at=dataset.bars[index].observed_at,
        instrument_id=dataset.bars[index].instrument_id,
        source=package.candidate.files[0],
        sdk_files=package.sdk_files,
        history=tuple(ClosedBar(**vars(bar)) for bar in history),
        history_sha256=fingerprint,
    )


def prepare_invocation(package: ValidationPackage, index: int) -> CandidateInvocation:
    """Return one immutable inert envelope, not a whole-worker dataset or execution grant."""
    package, dataset = _inputs(package)
    return _invocation(
        package, dataset, index, package.package_ref, package.candidate.candidate_ref
    )


def assemble_unattested_tape(
    package: ValidationPackage,
    outputs: tuple[CandidateTargetOutput, ...] | list[CandidateTargetOutput],
) -> SignalTape:
    """Reconstruct exact coverage from untrusted targets; no origin or execution attestation."""
    package, dataset = _inputs(package)
    if type(outputs) not in (tuple, list) or len(outputs) != len(dataset.bars) - 2:
        raise ValueError("Target inventory must cover every non-warmup invocation exactly once")
    by_ref = {}
    for output in outputs:
        if type(output) is not CandidateTargetOutput:
            raise ValueError("Target rows must be checked output models")
        checked = CandidateTargetOutput.model_validate_json(output.model_dump_json())
        if checked.invocation_ref in by_ref:
            raise ValueError("Duplicate target invocation")
        by_ref[checked.invocation_ref] = checked.target
    package_ref, candidate_ref = package.package_ref, package.candidate.candidate_ref
    decisions = []
    for index, bar in enumerate(dataset.bars):
        with localcontext(Context(prec=28)):
            history_sha256 = BarDataset(dataset.bars[max(0, index - 2) : index]).fingerprint
        target = 0
        if index >= 2:
            invocation = _invocation(package, dataset, index, package_ref, candidate_ref)
            if invocation.invocation_ref not in by_ref:
                raise ValueError("Missing or foreign target invocation")
            target = by_ref.pop(invocation.invocation_ref)
        decisions.append(
            SignalDecision(execute_at=bar.observed_at, history_sha256=history_sha256, target=target)
        )
    if by_ref:
        raise ValueError("Unexplained target invocation")
    tape = SignalTape(
        package_ref=package_ref,
        candidate_ref=candidate_ref,
        dataset_ref=package.holdout.receipt.dataset_ref,
        dataset_fingerprint=package.holdout.receipt.dataset_fingerprint,
        decisions=tuple(decisions),
    )
    tape.require_package(package)
    return tape
