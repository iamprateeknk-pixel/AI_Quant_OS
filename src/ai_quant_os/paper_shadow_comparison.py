"""Exact, source-linked daily comparison of candidate PAPER and shadow fills."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext
from hashlib import sha256
from typing import Literal

from ai_quant_os.daily_execution_evidence import DailyExecutionEvidence
from ai_quant_os.evidence_store import encode_evidence
from ai_quant_os.shadow_portfolio import ShadowReport


@dataclass(frozen=True)
class PaperShadowComparison:
    day: date
    candidate_id: str
    genome_sha256: str
    strategy_ref: str
    instrument_id: str
    quote_currency: str
    paper_evidence_sha256: str
    prior_shadow_sha256: str
    current_shadow_sha256: str
    paper_fill_count: int
    shadow_fill_count: int
    paper_cash_flow_quote: Decimal
    shadow_cash_flow_quote: Decimal
    shadow_minus_paper_cash_flow_quote: Decimal
    paper_fee_cash_flow_quote: Decimal
    shadow_fee_cash_flow_quote: Decimal
    shadow_minus_paper_fee_cash_flow_quote: Decimal
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER_SHADOW_DIAGNOSTIC"] = "PAPER_SHADOW_DIAGNOSTIC"
    promotion_allowed: Literal[False] = False
    causal_attribution_allowed: Literal[False] = False

    def __post_init__(self):
        amounts = (
            self.paper_cash_flow_quote,
            self.shadow_cash_flow_quote,
            self.shadow_minus_paper_cash_flow_quote,
            self.paper_fee_cash_flow_quote,
            self.shadow_fee_cash_flow_quote,
            self.shadow_minus_paper_fee_cash_flow_quote,
        )
        if any(type(value) is not Decimal or not value.is_finite() for value in amounts):
            raise ValueError("Comparison cash flows must be finite Decimal values")
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            cash_gap = self.shadow_cash_flow_quote - self.paper_cash_flow_quote
            fee_gap = self.shadow_fee_cash_flow_quote - self.paper_fee_cash_flow_quote
        hashes = (
            self.candidate_id,
            self.genome_sha256,
            self.paper_evidence_sha256,
            self.prior_shadow_sha256,
            self.current_shadow_sha256,
        )
        if (
            type(self.day) is not date
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in hashes
            )
            or any(
                not isinstance(value, str) or not value
                for value in (self.strategy_ref, self.instrument_id, self.quote_currency)
            )
            or type(self.paper_fill_count) is not int
            or type(self.shadow_fill_count) is not int
            or min(self.paper_fill_count, self.shadow_fill_count) < 0
            or self.shadow_minus_paper_cash_flow_quote != cash_gap
            or self.shadow_minus_paper_fee_cash_flow_quote != fee_gap
            or self.schema_version != "1.0"
            or self.mode != "PAPER_SHADOW_DIAGNOSTIC"
            or self.promotion_allowed is not False
            or self.causal_attribution_allowed is not False
        ):
            raise ValueError("Invalid PAPER/shadow comparison")


def _cash_flow(fills):
    cash_flow = Decimal(0)
    fees = Decimal(0)
    for fill in fills:
        signed_quantity = fill.quantity if fill.side == "BUY" else -fill.quantity
        cash_flow -= signed_quantity * fill.price + fill.fee
        fees -= fill.fee
    return cash_flow, fees


def compare_daily_paper_to_shadow(daily_execution, prior, current):
    """Compare exact fill cash flows for one UTC day and one linked shadow batch.

    This compares observed fills only. It does not claim complete market coverage,
    explain causality, or grant promotion authority.
    """
    return compare_daily_paper_to_shadow_batches(daily_execution, prior, (current,))


def compare_daily_paper_to_shadow_batches(daily_execution, prior, batches):
    """Compare a day's PAPER evidence with every linked shadow batch in that day."""
    if (
        type(daily_execution) is not DailyExecutionEvidence
        or type(prior) is not ShadowReport
        or type(batches) is not tuple
        or not batches
        or any(type(report) is not ShadowReport for report in batches)
        or prior.outcome != "OBSERVED"
        or prior.run is None
        or any(report.outcome != "OBSERVED" or report.run is None for report in batches)
    ):
        raise ValueError("Observed daily PAPER and shadow evidence is required")

    day = daily_execution.daily.report.day
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    current = batches[-1]
    assignment = current.assignment
    candidate_id = assignment.candidate.manifest.candidate_id
    genome = assignment.candidate.report.holdout.binding.genome
    if prior.assignment != assignment:
        raise ValueError("Shadow reports must link the same UTC day interval")
    previous = prior
    for report in batches:
        appended = report.request.bars
        if (
            report.assignment != assignment
            or report.sequence != previous.sequence + 1
            or report.previous_sha256 != previous.fingerprint
            or report.source_bars[: len(previous.source_bars)] != previous.source_bars
            or len(report.source_bars) != len(previous.source_bars) + len(appended)
            or not appended
            or any(not start <= bar.observed_at < end for bar in appended)
            or report.source_bars[-len(appended) :] != appended
        ):
            raise ValueError("Shadow reports must link the same UTC day interval")
        previous = report
    if not prior.source_bars or prior.source_bars[-1].observed_at >= start:
        raise ValueError("Shadow reports must link the same UTC day interval")
    prior_fills = prior.run.result.fills
    current_fills = current.run.result.fills
    if current_fills[: len(prior_fills)] != prior_fills:
        raise ValueError("Shadow fill history does not preserve its prior prefix")
    shadow_fills = current_fills[len(prior_fills) :]
    if any(not start <= fill.observed_at < end for fill in shadow_fills):
        raise ValueError("Shadow fills must belong to the same UTC day")

    owners = tuple(owner for owner in daily_execution.owners if owner.candidate_id == candidate_id)
    if not owners or any(
        owner.genome_sha256 != genome.fingerprint
        or f"{owner.strategy_id}@{owner.strategy_version}" != genome.strategy_ref
        or owner.instrument_id != assignment.candidate.report.plan.holdout_scope.instrument_id
        for owner in owners
    ):
        raise ValueError("Daily PAPER ownership does not match the shadow candidate genome")
    owner_by_order = {owner.order_id: owner for owner in owners}
    paper_fills = tuple(fill for fill in daily_execution.fills if fill.order_id in owner_by_order)
    instrument = assignment.candidate.report.plan.holdout_scope.instrument_id
    if (
        any(fill.instrument_id != instrument for fill in paper_fills)
        or current.run.result.instrument_id != instrument
    ):
        raise ValueError("PAPER and shadow fills must share the candidate instrument")
    paper_currency = next(
        row.quote_currency
        for row in daily_execution.daily.closing.catalog
        if row.instrument_id == instrument
    )
    if current.run.result.pnl_currency != paper_currency:
        raise ValueError("PAPER and shadow quote currencies must match")

    with localcontext(Context(prec=28)) as context:
        context.traps[Inexact] = True
        paper_cash, paper_fees = _cash_flow(paper_fills)
        shadow_cash, shadow_fees = _cash_flow(shadow_fills)
    return PaperShadowComparison(
        day=day,
        candidate_id=candidate_id,
        genome_sha256=genome.fingerprint,
        strategy_ref=genome.strategy_ref,
        instrument_id=instrument,
        quote_currency=paper_currency,
        paper_evidence_sha256=sha256(encode_evidence(daily_execution)).hexdigest(),
        prior_shadow_sha256=prior.fingerprint,
        current_shadow_sha256=current.fingerprint,
        paper_fill_count=len(paper_fills),
        shadow_fill_count=len(shadow_fills),
        paper_cash_flow_quote=paper_cash,
        shadow_cash_flow_quote=shadow_cash,
        shadow_minus_paper_cash_flow_quote=shadow_cash - paper_cash,
        paper_fee_cash_flow_quote=paper_fees,
        shadow_fee_cash_flow_quote=shadow_fees,
        shadow_minus_paper_fee_cash_flow_quote=shadow_fees - paper_fees,
    )
