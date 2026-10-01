"""UTC daily cash/inventory accounting from retained paper ledger checkpoints."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.currency_portfolio import CurrencyInstrument
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_attribution import (
    OrderAttribution,
    _account,
    _fingerprint,
    _mark,
    build_strategy_contributions,
)
from ai_quant_os.portfolio import PaperFill


@dataclass(frozen=True)
class EvidenceCheckpoint:
    checkpoint_id: str
    captured_at: datetime
    owners: tuple[OrderAttribution, ...]
    fills: tuple[PaperFill, ...]
    quotes: tuple[MarketSnapshot, ...]
    catalog: tuple[CurrencyInstrument, ...]
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"

    def __post_init__(self):
        _require_aware(self.captured_at)
        object.__setattr__(self, "captured_at", self.captured_at.astimezone(timezone.utc))
        if not isinstance(self.checkpoint_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.checkpoint_id
        ):
            raise ValueError("Invalid evidence checkpoint identity")
        if self.schema_version != "1.0" or self.mode != "PAPER":
            raise ValueError("Paper evidence only")
        for name, model, key in (
            ("owners", OrderAttribution, "order_id"),
            ("fills", PaperFill, "fill_id"),
            ("quotes", MarketSnapshot, "instrument_id"),
            ("catalog", CurrencyInstrument, "instrument_id"),
        ):
            values = getattr(self, name)
            if type(values) is not tuple or any(type(row) is not model for row in values):
                raise ValueError("Typed immutable checkpoint sources required")
            object.__setattr__(self, name, tuple(sorted(values, key=lambda row: getattr(row, key))))
        if any(owner.created_at > self.captured_at for owner in self.owners):
            raise ValueError("Checkpoint ownership cannot come from the future")
        if any(
            type(quote.trusted) is not bool
            or quote.instrument_id not in {row.instrument_id for row in self.catalog}
            for quote in self.quotes
        ):
            raise ValueError("Checkpoint quote metadata mismatch")
        build_strategy_contributions(
            self.owners, self.fills, self.quotes, self.catalog, now=self.captured_at
        )


@dataclass(frozen=True)
class DailyAccounting:
    fill_count: int
    opening_quantity: Decimal
    closing_quantity: Decimal
    cash_flow_before_fees_quote: Decimal
    fee_cash_flow_quote: Decimal
    cash_flow_quote: Decimal
    opening_inventory_quote: Decimal | None
    closing_inventory_quote: Decimal | None
    daily_pnl_quote: Decimal | None


@dataclass(frozen=True)
class DailyStrategyContribution(DailyAccounting):
    strategy_id: str
    strategy_version: str
    instrument_id: str
    quote_currency: str


@dataclass(frozen=True)
class DailyInstrumentContribution(DailyAccounting):
    instrument_id: str
    quote_currency: str
    opening_mark_price: Decimal | None
    closing_mark_price: Decimal | None
    opening_mark_state: str
    closing_mark_state: str


@dataclass(frozen=True)
class PaperDailyReport:
    day: date
    opening_ledger_sha256: str
    closing_ledger_sha256: str
    strategies: tuple[DailyStrategyContribution, ...]
    instruments: tuple[DailyInstrumentContribution, ...]
    state: Literal["COMPLETE_ACCOUNTING", "INCOMPLETE_MARKS"]
    evidence_gaps: tuple[str, ...]
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    accounting_method: Literal["UTC_DAY_CASH_PLUS_INVENTORY_CHANGE_V1"] = (
        "UTC_DAY_CASH_PLUS_INVENTORY_CHANGE_V1"
    )
    marking_method: Literal["COMMON_PORTFOLIO_LIQUIDATION_MARK_V1"] = (
        "COMMON_PORTFOLIO_LIQUIDATION_MARK_V1"
    )
    reconciled: Literal[True] = True
    promotion_allowed: Literal[False] = False


def _boundary_mark(checkpoint, instrument, quantity, boundary):
    if checkpoint.captured_at > boundary + timedelta(seconds=5):
        return None, "CAPTURE_LATE"
    quote = next((row for row in checkpoint.quotes if row.instrument_id == instrument), None)
    return _mark(quote, quantity, boundary, 5)


def _inventory(quantity, mark):
    return Decimal(0) if quantity == 0 else None if mark is None else quantity * mark


def _contribution(before, during, opening_mark, closing_mark):
    opening = _account(before, None)
    current = _account(during, None)
    opening_quantity = opening["net_quantity"]
    closing_quantity = opening_quantity + current["net_quantity"]
    opening_value = _inventory(opening_quantity, opening_mark)
    closing_value = _inventory(closing_quantity, closing_mark)
    return dict(
        fill_count=len(during),
        opening_quantity=opening_quantity,
        closing_quantity=closing_quantity,
        cash_flow_before_fees_quote=current["cash_flow_before_fees_quote"],
        fee_cash_flow_quote=current["fee_cash_flow_quote"],
        cash_flow_quote=current["cash_flow_quote"],
        opening_inventory_quote=opening_value,
        closing_inventory_quote=closing_value,
        daily_pnl_quote=None
        if opening_value is None or closing_value is None
        else current["cash_flow_quote"] + closing_value - opening_value,
    )


def _optional_sum(values):
    return None if any(value is None for value in values) else sum(values, Decimal(0))


def build_daily_report(day, opening, closing):
    if (
        type(day) is not date
        or type(opening) is not EvidenceCheckpoint
        or type(closing) is not EvidenceCheckpoint
    ):
        raise ValueError("UTC calendar day and verified checkpoints required")
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    if (
        not start <= opening.captured_at <= closing.captured_at
        or closing.captured_at < end
        or opening.catalog != closing.catalog
    ):
        raise ValueError("Daily evidence requires a closed UTC window and stable catalog")
    context = Context(prec=28)
    context.traps[Inexact] = True
    with localcontext(context):
        opening_history = tuple(fill for fill in opening.fills if fill.observed_at < start)
        closing_history = tuple(fill for fill in closing.fills if fill.observed_at < start)
        if opening_history != closing_history:
            raise ValueError("Recorded opening ledger changed through late or altered fills")
        earlier_owners = {row.order_id: row for row in opening.owners}
        ownership = {row.order_id: row for row in closing.owners}
        for fill in opening_history:
            old, current = earlier_owners[fill.order_id], ownership[fill.order_id]
            if (old.intent_sha256, old.order_sha256, old.strategy_id, old.strategy_version) != (
                current.intent_sha256,
                current.order_sha256,
                current.strategy_id,
                current.strategy_version,
            ):
                raise ValueError("Recorded opening ledger ownership changed")
        relevant = tuple(fill for fill in closing.fills if fill.observed_at < end)
        groups = {}
        for fill in relevant:
            owner = ownership[fill.order_id]
            groups.setdefault(
                (owner.strategy_id, owner.strategy_version, fill.instrument_id), []
            ).append(fill)
        strategies, totals, gaps = (
            [],
            [],
            [
                "AGENT_CREDIT_EVIDENCE_MISSING",
                "CANDIDATE_GENOME_BINDING_MISSING",
                "LIVE_SHADOW_COMPARISON_MISSING",
                "CAUSAL_FACTOR_ATTRIBUTION_MISSING",
            ],
        )
        currencies = {row.instrument_id: row.quote_currency for row in closing.catalog}
        for instrument in sorted({fill.instrument_id for fill in relevant}):
            history = tuple(fill for fill in relevant if fill.instrument_id == instrument)
            before = tuple(fill for fill in history if fill.observed_at < start)
            during = tuple(fill for fill in history if start <= fill.observed_at < end)
            op_quantity, cl_quantity = (
                _account(before, None)["net_quantity"],
                _account(history, None)["net_quantity"],
            )
            op_mark, op_state = _boundary_mark(opening, instrument, op_quantity, start)
            cl_mark, cl_state = _boundary_mark(closing, instrument, cl_quantity, end)
            rows = []
            for key in sorted(groups):
                if key[2] != instrument:
                    continue
                old = tuple(fill for fill in groups[key] if fill.observed_at < start)
                new = tuple(fill for fill in groups[key] if start <= fill.observed_at < end)
                rows.append(
                    DailyStrategyContribution(
                        **_contribution(old, new, op_mark, cl_mark),
                        strategy_id=key[0],
                        strategy_version=key[1],
                        instrument_id=instrument,
                        quote_currency=currencies[instrument],
                    )
                )
            total = _contribution(before, during, op_mark, cl_mark)
            for field in (
                "fill_count",
                "opening_quantity",
                "closing_quantity",
                "cash_flow_before_fees_quote",
                "fee_cash_flow_quote",
                "cash_flow_quote",
            ):
                if sum((getattr(row, field) for row in rows), Decimal(0)) != total[field]:
                    raise ValueError("Daily strategy contributions do not reconcile")
            for field in ("opening_inventory_quote", "closing_inventory_quote", "daily_pnl_quote"):
                summed = _optional_sum([getattr(row, field) for row in rows])
                if summed is not None and summed != total[field]:
                    raise ValueError("Daily marked contributions do not reconcile")
                total[field] = summed
            if total["opening_inventory_quote"] is None:
                gaps.append(f"OPENING_MARK_MISSING:{instrument}:{op_state}")
            if total["closing_inventory_quote"] is None:
                gaps.append(f"CLOSING_MARK_MISSING:{instrument}:{cl_state}")
            strategies.extend(rows)
            totals.append(
                DailyInstrumentContribution(
                    **total,
                    instrument_id=instrument,
                    quote_currency=currencies[instrument],
                    opening_mark_price=op_mark,
                    closing_mark_price=cl_mark,
                    opening_mark_state=op_state,
                    closing_mark_state=cl_state,
                )
            )
        return PaperDailyReport(
            day,
            _fingerprint(opening.owners, opening.fills),
            _fingerprint(closing.owners, closing.fills),
            tuple(strategies),
            tuple(totals),
            "INCOMPLETE_MARKS"
            if any(row.daily_pnl_quote is None for row in totals)
            else "COMPLETE_ACCOUNTING",
            tuple(sorted(set(gaps))),
        )


@dataclass(frozen=True)
class DailyEvidence:
    opening: EvidenceCheckpoint
    closing: EvidenceCheckpoint
    report: PaperDailyReport
    published_at: datetime

    def __post_init__(self):
        _require_aware(self.published_at)
        object.__setattr__(self, "published_at", self.published_at.astimezone(timezone.utc))
        if (
            type(self.report) is not PaperDailyReport
            or self.published_at < self.closing.captured_at
            or self.report != build_daily_report(self.report.day, self.opening, self.closing)
        ):
            raise ValueError("Daily evidence must reproduce its retained sources")
