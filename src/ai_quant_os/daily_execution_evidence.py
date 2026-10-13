"""Source-bound daily execution attribution from immutable paper evidence."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.daily_evidence import DailyEvidence
from ai_quant_os.execution_attribution import (
    DecisionPriceBenchmark,
    ExecutionContributionReport,
    build_execution_contributions,
)
from ai_quant_os.paper_attribution import OrderAttribution
from ai_quant_os.portfolio import PaperFill


@dataclass(frozen=True)
class DailyExecutionAttribution:
    day: date
    opening_checkpoint_id: str
    closing_checkpoint_id: str
    opening_ledger_sha256: str
    closing_ledger_sha256: str
    contribution: ExecutionContributionReport
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        if (
            type(self.day) is not date
            or not isinstance(self.opening_checkpoint_id, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.opening_checkpoint_id)
            or not isinstance(self.closing_checkpoint_id, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.closing_checkpoint_id)
            or not isinstance(self.opening_ledger_sha256, str)
            or not isinstance(self.closing_ledger_sha256, str)
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (self.opening_ledger_sha256, self.closing_ledger_sha256)
            )
            or type(self.contribution) is not ExecutionContributionReport
            or self.schema_version != "1.0"
            or self.mode != "PAPER"
            or self.promotion_allowed is not False
        ):
            raise ValueError("Invalid daily execution attribution")


@dataclass(frozen=True)
class DailyExecutionEvidence:
    daily: DailyEvidence
    owners: tuple[OrderAttribution, ...]
    fills: tuple[PaperFill, ...]
    benchmarks: tuple[DecisionPriceBenchmark, ...]
    report: DailyExecutionAttribution
    published_at: datetime
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"

    def __post_init__(self):
        _require_aware(self.published_at)
        object.__setattr__(self, "published_at", self.published_at.astimezone(timezone.utc))
        if (
            type(self.daily) is not DailyEvidence
            or type(self.report) is not DailyExecutionAttribution
            or self.published_at < self.daily.published_at
            or self.schema_version != "1.0"
            or self.mode != "PAPER"
            or self.report != _report(self.daily, self.owners, self.fills, self.benchmarks)
        ):
            raise ValueError("Daily execution evidence must reproduce its retained sources")


def _report(daily, owners, fills, benchmarks):
    if (
        type(owners) is not tuple
        or type(fills) is not tuple
        or type(benchmarks) is not tuple
        or any(type(row) is not OrderAttribution for row in owners)
        or any(type(row) is not PaperFill for row in fills)
        or any(type(row) is not DecisionPriceBenchmark for row in benchmarks)
    ):
        raise ValueError("Typed daily execution sources required")
    if (
        len(owners) > 10000
        or len(fills) > 10000
        or len(benchmarks) > 10000
        or len({row.order_id for row in owners}) != len(owners)
        or len({row.fill_id for row in fills}) != len(fills)
        or len({row.order_id for row in benchmarks}) != len(benchmarks)
    ):
        raise ValueError("Daily execution source exceeds identity or capacity bounds")

    start = datetime.combine(daily.report.day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    expected_fills = tuple(row for row in daily.closing.fills if start <= row.observed_at < end)
    if fills != expected_fills:
        raise ValueError("Daily execution fills differ from the published daily source")
    order_ids = {row.order_id for row in fills}
    expected_owners = tuple(row for row in daily.closing.owners if row.order_id in order_ids)
    if owners != expected_owners or {row.order_id for row in owners} != order_ids:
        raise ValueError("Daily execution ownership differs from the published daily source")
    if any(row.order_id not in order_ids for row in benchmarks):
        raise ValueError("Daily execution benchmark is unrelated to a daily fill")

    catalog = daily.closing.catalog
    contribution = build_execution_contributions(
        owners, fills, benchmarks, catalog, now=daily.closing.captured_at
    )
    return DailyExecutionAttribution(
        daily.report.day,
        daily.opening.checkpoint_id,
        daily.closing.checkpoint_id,
        daily.report.opening_ledger_sha256,
        daily.report.closing_ledger_sha256,
        contribution,
    )


def build_daily_execution_evidence(
    daily, ledger_owners, ledger_fills, ledger_benchmarks, *, published_at
):
    """Bind current execution history to the already published daily accounting sources."""
    if type(daily) is not DailyEvidence:
        raise ValueError("Published daily accounting evidence required")
    if (
        type(ledger_owners) is not tuple
        or type(ledger_fills) is not tuple
        or type(ledger_benchmarks) is not tuple
        or len(ledger_owners) > 10000
        or len(ledger_fills) > 10000
        or len(ledger_benchmarks) > 10000
        or any(type(row) is not OrderAttribution for row in ledger_owners)
        or any(type(row) is not PaperFill for row in ledger_fills)
        or any(type(row) is not DecisionPriceBenchmark for row in ledger_benchmarks)
        or len({row.order_id for row in ledger_owners}) != len(ledger_owners)
        or len({row.fill_id for row in ledger_fills}) != len(ledger_fills)
        or len({row.order_id for row in ledger_benchmarks}) != len(ledger_benchmarks)
    ):
        raise ValueError("Bounded execution ledger required")

    start = datetime.combine(daily.report.day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    opening_history = tuple(row for row in ledger_fills if row.observed_at < start)
    retained_opening_history = tuple(row for row in daily.opening.fills if row.observed_at < start)
    if opening_history != retained_opening_history:
        raise ValueError("Current execution history differs from the published daily source")

    fills = tuple(row for row in ledger_fills if start <= row.observed_at < end)
    order_ids = {row.order_id for row in fills}
    retained_owners = {row.order_id: row for row in daily.closing.owners}
    current_owners = {row.order_id: row for row in ledger_owners}
    owners = tuple(retained_owners[order_id] for order_id in sorted(order_ids))
    for order_id in order_ids:
        retained, current = retained_owners[order_id], current_owners.get(order_id)
        if current is None or (
            retained.intent_id,
            retained.strategy_id,
            retained.strategy_version,
            retained.instrument_id,
            retained.side,
            retained.quantity,
            retained.created_at,
            retained.expires_at,
            retained.intent_sha256,
        ) != (
            current.intent_id,
            current.strategy_id,
            current.strategy_version,
            current.instrument_id,
            current.side,
            current.quantity,
            current.created_at,
            current.expires_at,
            current.intent_sha256,
        ):
            raise ValueError("Current execution ownership differs from the published daily source")
    benchmarks = tuple(row for row in ledger_benchmarks if row.order_id in order_ids)
    report = _report(daily, owners, fills, benchmarks)
    return DailyExecutionEvidence(daily, owners, fills, benchmarks, report, published_at)
