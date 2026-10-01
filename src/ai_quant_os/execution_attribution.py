"""Initial-decision cash-flow decomposition, with explicit historical benchmark gaps."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, Inexact, localcontext
from hashlib import sha256
import json
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_attribution import build_strategy_contributions


@dataclass(frozen=True)
class DecisionPriceBenchmark:
    order_id: str
    intent_id: str
    risk_id: str
    decision_at: datetime
    market: MarketSnapshot
    schema_version: Literal["1.0"] = "1.0"
    provenance: Literal["RISK_KERNEL_INITIAL_DECISION_V1"] = "RISK_KERNEL_INITIAL_DECISION_V1"

    def __post_init__(self):
        _require_aware(self.decision_at)
        object.__setattr__(self, "decision_at", self.decision_at.astimezone(timezone.utc))
        if (
            self.schema_version != "1.0"
            or self.provenance != "RISK_KERNEL_INITIAL_DECISION_V1"
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 128
                for value in (self.order_id, self.intent_id, self.risk_id)
            )
            or type(self.market) is not MarketSnapshot
            or self.market.trusted is not True
            or self.market.observed_at > self.decision_at
        ):
            raise ValueError("Invalid initial decision-price benchmark")


def encode_benchmark(value: DecisionPriceBenchmark) -> bytes:
    def scalar(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError("Unsupported benchmark value")

    return json.dumps(
        asdict(value), default=scalar, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True)
class ExecutionCashComponents:
    fill_count: int
    benchmark_fill_count: int
    reference_mid_cash_flow_quote: Decimal | None
    spread_cash_adjustment_quote: Decimal | None
    decision_to_fill_cash_adjustment_quote: Decimal | None
    fee_cash_flow_quote: Decimal
    actual_cash_flow_quote: Decimal


@dataclass(frozen=True)
class StrategyExecutionContribution(ExecutionCashComponents):
    strategy_id: str
    strategy_version: str
    instrument_id: str
    quote_currency: str


@dataclass(frozen=True)
class InstrumentExecutionContribution(ExecutionCashComponents):
    instrument_id: str
    quote_currency: str


@dataclass(frozen=True)
class ExecutionContributionReport:
    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    as_of: datetime
    ledger_sha256: str
    benchmarks_sha256: str
    benchmark_method: Literal["INITIAL_DECISION_MID_SIDE_QUOTE_V1"]
    coverage: Literal["COMPLETE", "PARTIAL", "NONE", "EMPTY"]
    strategies: tuple[StrategyExecutionContribution, ...]
    instruments: tuple[InstrumentExecutionContribution, ...]
    missing_benchmark_order_ids: tuple[str, ...]
    reconciled: Literal[True]
    evidence_gaps: tuple[str, ...]


def _components(fills, benchmarks):
    mid_cash, spread, difference, fees, actual = (Decimal(0) for _ in range(5))
    count = 0
    for fill in fills:
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        fees -= fill.fee
        actual -= signed * fill.price + fill.fee
        benchmark = benchmarks.get(fill.order_id)
        if benchmark is not None:
            midpoint = (benchmark.market.bid + benchmark.market.ask) / 2
            side_price = benchmark.market.ask if fill.side == "BUY" else benchmark.market.bid
            mid_cash -= signed * midpoint
            spread -= signed * (side_price - midpoint)
            difference -= signed * (fill.price - side_price)
            count += 1
    complete = count == len(fills)
    if complete and mid_cash + spread + difference + fees != actual:
        raise ValueError("Execution benchmark cash flows do not reconcile")
    return dict(
        fill_count=len(fills),
        benchmark_fill_count=count,
        reference_mid_cash_flow_quote=mid_cash if complete else None,
        spread_cash_adjustment_quote=spread if complete else None,
        decision_to_fill_cash_adjustment_quote=difference if complete else None,
        fee_cash_flow_quote=fees,
        actual_cash_flow_quote=actual,
    )


def build_execution_contributions(owners, fills, benchmarks, catalog, *, now):
    with localcontext(Context(prec=28)) as context:
        context.traps[Inexact] = True
        accounting = build_strategy_contributions(owners, fills, (), catalog, now=now)
        if len(benchmarks) > 10000 or len({row.order_id for row in benchmarks}) != len(benchmarks):
            raise ValueError("Execution benchmark inventory exceeds identity or capacity bounds")
        ownership = {row.order_id: row for row in owners}
        retained = {row.order_id: row for row in benchmarks}
        for row in benchmarks:
            owner = ownership.get(row.order_id)
            if (
                owner is None
                or (row.intent_id, row.market.instrument_id)
                != (owner.intent_id, owner.instrument_id)
                or not owner.created_at <= row.decision_at < owner.expires_at
                or row.decision_at > now
            ):
                raise ValueError("Execution benchmark ownership or timing mismatch")
        for fill in fills:
            row = retained.get(fill.order_id)
            if row is not None and fill.observed_at < row.decision_at:
                raise ValueError("Execution benchmark was not known before the fill")
        grouped = {}
        for fill in fills:
            owner = ownership[fill.order_id]
            grouped.setdefault(
                (owner.strategy_id, owner.strategy_version, owner.instrument_id), []
            ).append(fill)
        strategies = tuple(
            StrategyExecutionContribution(
                **_components(
                    grouped[(row.strategy_id, row.strategy_version, row.instrument_id)], retained
                ),
                strategy_id=row.strategy_id,
                strategy_version=row.strategy_version,
                instrument_id=row.instrument_id,
                quote_currency=row.quote_currency,
            )
            for row in accounting.strategies
        )
        totals = []
        for instrument in accounting.instruments:
            subset = tuple(fill for fill in fills if fill.instrument_id == instrument.instrument_id)
            total = InstrumentExecutionContribution(
                **_components(subset, retained),
                instrument_id=instrument.instrument_id,
                quote_currency=instrument.quote_currency,
            )
            rows = tuple(row for row in strategies if row.instrument_id == total.instrument_id)
            for field in (
                "actual_cash_flow_quote",
                "fee_cash_flow_quote",
                "fill_count",
                "benchmark_fill_count",
            ):
                if sum((getattr(row, field) for row in rows), Decimal(0)) != getattr(total, field):
                    raise ValueError("Execution contributions do not reconcile")
            if total.actual_cash_flow_quote != instrument.cash_flow_quote:
                raise ValueError("Execution attribution differs from original paper ledger")
            if total.benchmark_fill_count == total.fill_count:
                for field in (
                    "reference_mid_cash_flow_quote",
                    "spread_cash_adjustment_quote",
                    "decision_to_fill_cash_adjustment_quote",
                ):
                    if sum((getattr(row, field) for row in rows), Decimal(0)) != getattr(
                        total, field
                    ):
                        raise ValueError("Execution benchmark contributions do not reconcile")
            totals.append(total)
        count = sum(row.benchmark_fill_count for row in strategies)
        coverage = (
            "EMPTY"
            if not fills
            else "NONE"
            if not count
            else "COMPLETE"
            if count == len(fills)
            else "PARTIAL"
        )
        gaps = ["FILL_TIME_MARKET_BENCHMARK_MISSING", "LATENCY_CAUSAL_ATTRIBUTION_MISSING"]
        if coverage != "COMPLETE":
            gaps.append("INITIAL_DECISION_BENCHMARK_EVIDENCE_MISSING")
        if any(fill.source == "PAPER_MANUAL" for fill in fills):
            gaps.append("MANUAL_PAPER_FILL_EVIDENCE")
        raw = (
            b"["
            + b",".join(
                encode_benchmark(row) for row in sorted(benchmarks, key=lambda row: row.order_id)
            )
            + b"]"
        )
        return ExecutionContributionReport(
            "1.0",
            "PAPER",
            accounting.as_of,
            accounting.ledger_sha256,
            sha256(raw).hexdigest(),
            "INITIAL_DECISION_MID_SIDE_QUOTE_V1",
            coverage,
            strategies,
            tuple(totals),
            tuple(sorted({fill.order_id for fill in fills if fill.order_id not in retained})),
            True,
            tuple(gaps),
        )
