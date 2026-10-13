from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Inexact, Decimal, localcontext
from hashlib import sha256
import re
from typing import Literal

from ai_quant_os.daily_execution_evidence import DailyExecutionEvidence
from ai_quant_os.evidence_store import encode_evidence


@dataclass(frozen=True)
class DailyExecutionSource:
    day: date
    sha256: str

    def __post_init__(self):
        if (
            type(self.day) is not date
            or not isinstance(self.sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.sha256) is None
        ):
            raise ValueError("Invalid daily execution source reference")


@dataclass(frozen=True)
class StrategyExecutionAggregate:
    strategy_id: str
    strategy_version: str
    instrument_id: str
    quote_currency: str
    fill_count: int
    benchmark_fill_count: int
    actual_cash_flow_quote: Decimal
    fee_cash_flow_quote: Decimal
    reference_mid_cash_flow_quote: Decimal | None
    spread_cash_adjustment_quote: Decimal | None
    decision_to_fill_cash_adjustment_quote: Decimal | None

    def __post_init__(self):
        if (
            any(
                not isinstance(value, str) or not value
                for value in (
                    self.strategy_id,
                    self.strategy_version,
                    self.instrument_id,
                    self.quote_currency,
                )
            )
            or type(self.fill_count) is not int
            or type(self.benchmark_fill_count) is not int
            or not 0 <= self.benchmark_fill_count <= self.fill_count
            or any(
                type(value) is not Decimal or not value.is_finite()
                for value in (self.actual_cash_flow_quote, self.fee_cash_flow_quote)
            )
            or any(
                value is not None and (type(value) is not Decimal or not value.is_finite())
                for value in (
                    self.reference_mid_cash_flow_quote,
                    self.spread_cash_adjustment_quote,
                    self.decision_to_fill_cash_adjustment_quote,
                )
            )
            or (
                self.benchmark_fill_count < self.fill_count
                and any(
                    value is not None
                    for value in (
                        self.reference_mid_cash_flow_quote,
                        self.spread_cash_adjustment_quote,
                        self.decision_to_fill_cash_adjustment_quote,
                    )
                )
            )
        ):
            raise ValueError("Invalid daily strategy execution aggregate")


@dataclass(frozen=True)
class DailyExecutionAggregateReport:
    start_day: date
    end_day: date
    sources: tuple[DailyExecutionSource, ...]
    covered_days: tuple[date, ...]
    missing_days: tuple[date, ...]
    strategies: tuple[StrategyExecutionAggregate, ...]
    coverage: Literal["COMPLETE", "PARTIAL", "NO_EVIDENCE"]
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        if (
            type(self.start_day) is not date
            or type(self.end_day) is not date
            or self.end_day < self.start_day
            or (self.end_day - self.start_day).days >= 366
            or type(self.sources) is not tuple
            or any(type(row) is not DailyExecutionSource for row in self.sources)
            or type(self.covered_days) is not tuple
            or self.covered_days != tuple(row.day for row in self.sources)
            or type(self.missing_days) is not tuple
            or any(type(day) is not date for day in self.missing_days)
            or type(self.strategies) is not tuple
            or any(type(row) is not StrategyExecutionAggregate for row in self.strategies)
            or tuple(row.day for row in self.sources)
            != tuple(sorted({row.day for row in self.sources}))
            or tuple(self.missing_days) != tuple(sorted(set(self.missing_days)))
            or set(row.day for row in self.sources) & set(self.missing_days)
            or set(row.day for row in self.sources) | set(self.missing_days)
            != {
                self.start_day + timedelta(days=offset)
                for offset in range((self.end_day - self.start_day).days + 1)
            }
            or tuple(
                (row.strategy_id, row.strategy_version, row.instrument_id, row.quote_currency)
                for row in self.strategies
            )
            != tuple(
                sorted(
                    {
                        (
                            row.strategy_id,
                            row.strategy_version,
                            row.instrument_id,
                            row.quote_currency,
                        )
                        for row in self.strategies
                    }
                )
            )
            or self.coverage
            != (
                "NO_EVIDENCE"
                if not self.sources
                else "PARTIAL"
                if self.missing_days
                or any(row.benchmark_fill_count < row.fill_count for row in self.strategies)
                else "COMPLETE"
            )
            or self.schema_version != "1.0"
            or self.mode != "PAPER"
            or self.promotion_allowed is not False
        ):
            raise ValueError("Invalid daily execution aggregate report")


def aggregate_daily_execution(start_day, end_day, evidence):
    if (
        type(start_day) is not date
        or type(end_day) is not date
        or end_day < start_day
        or (end_day - start_day).days >= 366
        or type(evidence) is not tuple
        or len(evidence) > 366
        or any(type(row) is not DailyExecutionEvidence for row in evidence)
        or len({row.report.day for row in evidence}) != len(evidence)
        or any(not start_day <= row.report.day <= end_day for row in evidence)
    ):
        raise ValueError("Bounded unique daily execution evidence required")
    selected = tuple(sorted(evidence, key=lambda row: row.report.day))
    by_strategy = {}
    with localcontext(Context(prec=80)) as context:
        context.traps[Inexact] = True
        for day in selected:
            for row in day.report.contribution.strategies:
                key = (row.strategy_id, row.strategy_version, row.instrument_id, row.quote_currency)
                total = by_strategy.setdefault(
                    key,
                    {
                        "fill_count": 0,
                        "benchmark_fill_count": 0,
                        "actual_cash_flow_quote": Decimal(0),
                        "fee_cash_flow_quote": Decimal(0),
                        "reference_mid_cash_flow_quote": Decimal(0),
                        "spread_cash_adjustment_quote": Decimal(0),
                        "decision_to_fill_cash_adjustment_quote": Decimal(0),
                        "benchmarked": True,
                    },
                )
                total["fill_count"] += row.fill_count
                total["benchmark_fill_count"] += row.benchmark_fill_count
                total["actual_cash_flow_quote"] += row.actual_cash_flow_quote
                total["fee_cash_flow_quote"] += row.fee_cash_flow_quote
                total["benchmarked"] &= row.benchmark_fill_count == row.fill_count
                for field in (
                    "reference_mid_cash_flow_quote",
                    "spread_cash_adjustment_quote",
                    "decision_to_fill_cash_adjustment_quote",
                ):
                    amount = getattr(row, field)
                    if amount is None:
                        total["benchmarked"] = False
                    else:
                        total[field] += amount
    strategies = tuple(
        StrategyExecutionAggregate(
            *key[:3],
            key[3],
            values["fill_count"],
            values["benchmark_fill_count"],
            values["actual_cash_flow_quote"],
            values["fee_cash_flow_quote"],
            *(
                values[field] if values["benchmarked"] else None
                for field in (
                    "reference_mid_cash_flow_quote",
                    "spread_cash_adjustment_quote",
                    "decision_to_fill_cash_adjustment_quote",
                )
            ),
        )
        for key, values in sorted(by_strategy.items())
    )
    days = {row.report.day for row in selected}
    expected = {
        start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)
    }
    return DailyExecutionAggregateReport(
        start_day,
        end_day,
        tuple(
            DailyExecutionSource(row.report.day, sha256(encode_evidence(row)).hexdigest())
            for row in selected
        ),
        tuple(sorted(days)),
        tuple(sorted(expected - days)),
        strategies,
        "NO_EVIDENCE"
        if not selected
        else "PARTIAL"
        if expected - days or any(row.benchmark_fill_count < row.fill_count for row in strategies)
        else "COMPLETE",
    )
