"""Declared adjacent-window paper execution-cost diagnostics, never causal attribution."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.execution_attribution import DecisionPriceBenchmark, build_execution_contributions
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.portfolio import PaperFill

SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class DriftPolicy:
    window_seconds: int
    minimum_fills: int
    adverse_cost_increase_bps: Decimal

    def __post_init__(self):
        value = self.adverse_cost_increase_bps
        if (
            type(self.window_seconds) is not int
            or not 60 <= self.window_seconds <= 86400
            or type(self.minimum_fills) is not int
            or not 2 <= self.minimum_fills <= 512
            or type(value) is not Decimal
            or not value.is_finite()
            or not 0 <= value <= 10000
            or len(value.as_tuple().digits) > 16
            or not -6 <= value.as_tuple().exponent <= 6
        ):
            raise ValueError("Invalid declared execution drift policy")
        with localcontext(Context(prec=80)):
            object.__setattr__(self, "adverse_cost_increase_bps", value.quantize(SCALE))

    @property
    def content_ref(self) -> str:
        material = [self.window_seconds, self.minimum_fills, str(self.adverse_cost_increase_bps)]
        return (
            "execution-drift-policy:"
            + sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
        )


@dataclass(frozen=True)
class ExecutionCostWindow:
    fill_count: int
    benchmark_fill_count: int
    initial_mid_gross_notional_quote: Decimal | None
    spread_cost_bps: Decimal | None
    slippage_cost_bps: Decimal | None
    fee_cost_bps: Decimal | None
    cost_bps: Decimal | None


@dataclass(frozen=True)
class ExecutionDriftAssessment:
    strategy_id: str
    strategy_version: str
    instrument_id: str
    quote_currency: str
    baseline: ExecutionCostWindow
    current: ExecutionCostWindow
    spread_delta_bps: Decimal | None
    slippage_delta_bps: Decimal | None
    fee_delta_bps: Decimal | None
    cost_delta_bps: Decimal | None
    state: Literal["COST_INCREASE", "NO_COST_INCREASE", "UNASSESSED"]
    reasons: tuple[str, ...]
    recommendation: Literal["HUMAN_REVIEW_ONLY", "NO_AUTOMATIC_ACTION"]


@dataclass(frozen=True)
class ExecutionDriftReport:
    as_of: datetime
    ledger_sha256: str
    benchmarks_sha256: str
    policy_ref: str | None
    baseline_start: datetime | None
    current_start: datetime | None
    state: Literal["DECLARED_COMPARISON", "POLICY_UNCONFIGURED"]
    assessments: tuple[ExecutionDriftAssessment, ...]
    method: Literal["INITIAL_MID_NOTIONAL_WEIGHTED_COST_BPS_6DP_V1"] = (
        "INITIAL_MID_NOTIONAL_WEIGHTED_COST_BPS_6DP_V1"
    )


def _window(
    fills: list[PaperFill], benchmarks: dict[str, DecisionPriceBenchmark]
) -> ExecutionCostWindow:
    matched = sum(row.order_id in benchmarks for row in fills)
    if not fills or matched != len(fills):
        return ExecutionCostWindow(len(fills), matched, None, None, None, None, None)
    gross = spread = slippage = fees = Decimal(0)
    for fill in fills:
        market = benchmarks[fill.order_id].market
        mid = (market.bid + market.ask) / 2
        side = market.ask if fill.side == "BUY" else market.bid
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        gross += fill.quantity * mid
        spread += signed * (side - mid)
        slippage += signed * (fill.price - side)
        fees += fill.fee
    components = tuple(
        (value / gross * 10000).quantize(SCALE, rounding=ROUND_HALF_EVEN)
        for value in (spread, slippage, fees)
    )
    return ExecutionCostWindow(len(fills), matched, gross, *components, sum(components))


def compare_execution_drift(
    owners: tuple[OrderAttribution, ...],
    fills: tuple[PaperFill, ...],
    benchmarks: tuple[DecisionPriceBenchmark, ...],
    *,
    now: datetime,
    policy: DriftPolicy | None,
) -> ExecutionDriftReport:
    _require_aware(now)
    now = now.astimezone(timezone.utc)
    if len(owners) > 512 or len(fills) > 512 or len(benchmarks) > 512:
        raise ValueError("Execution drift source capacity exceeded")
    checked = build_execution_contributions(owners, fills, benchmarks, PAPER_INSTRUMENTS, now=now)
    if policy is None:
        return ExecutionDriftReport(
            now,
            checked.ledger_sha256,
            checked.benchmarks_sha256,
            None,
            None,
            None,
            "POLICY_UNCONFIGURED",
            (),
        )
    if type(policy) is not DriftPolicy:
        raise ValueError("Declared drift policy required")
    current_start = now - timedelta(seconds=policy.window_seconds)
    baseline_start = current_start - timedelta(seconds=policy.window_seconds)
    ownership = {row.order_id: row for row in owners}
    retained = {row.order_id: row for row in benchmarks}
    groups = {}
    for fill in fills:
        if baseline_start <= fill.observed_at <= now:
            owner = ownership[fill.order_id]
            groups.setdefault(
                (owner.strategy_id, owner.strategy_version, fill.instrument_id), []
            ).append(fill)
    if len(groups) > 128:
        raise ValueError("Execution drift group capacity exceeded")
    currencies = {row.instrument_id: row.quote_currency for row in PAPER_INSTRUMENTS}
    rows = []
    with localcontext(Context(prec=80)):
        for identity, selected in sorted(groups.items()):
            before = _window([row for row in selected if row.observed_at < current_start], retained)
            current = _window(
                [row for row in selected if row.observed_at >= current_start], retained
            )
            reasons = []
            if (
                before.benchmark_fill_count != before.fill_count
                or current.benchmark_fill_count != current.fill_count
            ):
                reasons.append("MISSING_BENCHMARKS")
            if before.fill_count < policy.minimum_fills:
                reasons.append("INSUFFICIENT_BASELINE")
            if current.fill_count < policy.minimum_fills:
                reasons.append("INSUFFICIENT_CURRENT")
            deltas = (
                (None,) * 4
                if reasons
                else tuple(
                    getattr(current, name) - getattr(before, name)
                    for name in ("spread_cost_bps", "slippage_cost_bps", "fee_cost_bps", "cost_bps")
                )
            )
            state = (
                "UNASSESSED"
                if reasons
                else "COST_INCREASE"
                if deltas[-1] > 0 and deltas[-1] >= policy.adverse_cost_increase_bps
                else "NO_COST_INCREASE"
            )
            rows.append(
                ExecutionDriftAssessment(
                    *identity,
                    currencies[identity[2]],
                    before,
                    current,
                    *deltas,
                    state,
                    tuple(reasons),
                    "HUMAN_REVIEW_ONLY" if state == "COST_INCREASE" else "NO_AUTOMATIC_ACTION",
                )
            )
    return ExecutionDriftReport(
        now,
        checked.ledger_sha256,
        checked.benchmarks_sha256,
        policy.content_ref,
        baseline_start,
        current_start,
        "DECLARED_COMPARISON",
        tuple(rows),
    )
