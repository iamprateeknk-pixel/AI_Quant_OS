"""Deterministic cumulative strategy accounting over an empty-opening paper ledger."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
from hashlib import sha256
import json
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.portfolio import summarize_position


@dataclass(frozen=True)
class OrderAttribution:
    order_id: str
    intent_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    status: str
    created_at: datetime
    expires_at: datetime
    intent_sha256: str
    order_sha256: str

    def __post_init__(self):
        for name in ("created_at", "expires_at"):
            _require_aware(getattr(self, name))
            object.__setattr__(self, name, getattr(self, name).astimezone(timezone.utc))
        if (
            any(
                not isinstance(value, str) or not 1 <= len(value) <= 128
                for value in (
                    self.order_id,
                    self.intent_id,
                    self.strategy_id,
                    self.strategy_version,
                    self.instrument_id,
                )
            )
            or self.side not in ("BUY", "SELL")
            or self.status not in ("PENDING", "DISPATCHING", "ACCEPTED", "REJECTED", "UNKNOWN")
            or not self.quantity.is_finite()
            or self.quantity <= 0
            or self.created_at >= self.expires_at
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (self.intent_sha256, self.order_sha256)
            )
        ):
            raise ValueError("Invalid paper attribution ownership")


@dataclass(frozen=True)
class AccountingContribution:
    fill_count: int
    net_quantity: Decimal
    cash_flow_before_fees_quote: Decimal
    fee_cash_flow_quote: Decimal
    cash_flow_quote: Decimal
    marked_inventory_quote: Decimal | None
    marked_pnl_quote: Decimal | None


@dataclass(frozen=True)
class StrategyContribution(AccountingContribution):
    strategy_id: str
    strategy_version: str
    instrument_id: str
    quote_currency: str


@dataclass(frozen=True)
class InstrumentContribution(AccountingContribution):
    instrument_id: str
    quote_currency: str
    mark_price: Decimal | None
    mark_state: str


@dataclass(frozen=True)
class StrategyContributionReport:
    schema_version: Literal["1.0"]
    mode: Literal["PAPER"]
    as_of: datetime
    ledger_sha256: str
    accounting_scope: Literal["CUMULATIVE_FROM_EMPTY_INVENTORY"]
    marking_method: Literal["COMMON_PORTFOLIO_LIQUIDATION_MARK_V1"]
    strategies: tuple[StrategyContribution, ...]
    instruments: tuple[InstrumentContribution, ...]
    reconciled: Literal[True]
    evidence_gaps: tuple[str, ...]


def _mark(quote, net, now, max_age_seconds):
    if quote is None:
        return None, "MISSING"
    if not quote.trusted:
        return None, "UNTRUSTED"
    age = (now - quote.observed_at).total_seconds()
    if age < 0:
        return None, "FUTURE"
    if age > max_age_seconds:
        return None, "STALE"
    return (quote.bid if net >= 0 else quote.ask), "AVAILABLE"


def _account(fills, mark):
    position = summarize_position(tuple(fills), mark=mark)
    fee_flow = -sum((fill.fee for fill in fills), Decimal(0))
    before_fees = sum(
        ((-fill.quantity if fill.side == "BUY" else fill.quantity) * fill.price for fill in fills),
        Decimal(0),
    )
    if before_fees + fee_flow != position.cash_flow_quote:
        raise ValueError("Paper attribution cash flows cannot be reconciled exactly")
    return dict(
        fill_count=len(fills),
        net_quantity=position.net_quantity,
        cash_flow_before_fees_quote=before_fees,
        fee_cash_flow_quote=fee_flow,
        cash_flow_quote=position.cash_flow_quote,
        marked_inventory_quote=position.net_exposure_quote,
        marked_pnl_quote=position.marked_pnl_quote,
    )


def _fingerprint(owners, fills):
    def scalar(value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError("Unsupported paper attribution value")

    raw = json.dumps(
        {
            "owners": [asdict(row) for row in sorted(owners, key=lambda row: row.order_id)],
            "fills": [asdict(row) for row in sorted(fills, key=lambda row: row.fill_id)],
        },
        default=scalar,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(raw).hexdigest()


def build_strategy_contributions(
    owners, fills, quotes, catalog, *, now: datetime, max_age_seconds: int = 5
):
    _require_aware(now)
    with localcontext(Context(prec=28)):
        if (
            len(owners) > 10000
            or len(fills) > 10000
            or len(quotes) > 16
            or len(catalog) > 16
            or type(max_age_seconds) is not int
            or not 1 <= max_age_seconds <= 300
        ):
            raise ValueError("Paper attribution capacity or freshness bound exceeded")
        ownership = {row.order_id: row for row in owners}
        instruments = {row.instrument_id: row for row in catalog}
        prices = {row.instrument_id: row for row in quotes}
        if (
            len(ownership) != len(owners)
            or len(instruments) != len(catalog)
            or len(prices) != len(quotes)
            or len({fill.fill_id for fill in fills}) != len(fills)
        ):
            raise ValueError("Duplicate paper attribution identity")
        quantities, grouped = {}, {}
        for fill in fills:
            owner = ownership.get(fill.order_id)
            if (
                owner is None
                or owner.status != "ACCEPTED"
                or fill.instrument_id not in instruments
                or (fill.instrument_id, fill.side) != (owner.instrument_id, owner.side)
                or not owner.created_at <= fill.observed_at <= min(owner.expires_at, now)
            ):
                raise ValueError("Paper attribution fill lineage mismatch")
            quantities[owner.order_id] = quantities.get(owner.order_id, Decimal(0)) + fill.quantity
            if quantities[owner.order_id] > owner.quantity:
                raise ValueError("Paper attribution fill exceeds owned quantity")
            grouped.setdefault(
                (owner.strategy_id, owner.strategy_version, fill.instrument_id), []
            ).append(fill)
        strategies, totals = [], []
        for instrument_id in sorted({fill.instrument_id for fill in fills}):
            instrument_fills = tuple(fill for fill in fills if fill.instrument_id == instrument_id)
            net = summarize_position(instrument_fills, mark=None).net_quantity
            mark, state = _mark(prices.get(instrument_id), net, now, max_age_seconds)
            currency = instruments[instrument_id].quote_currency
            total = InstrumentContribution(
                **_account(instrument_fills, mark),
                instrument_id=instrument_id,
                quote_currency=currency,
                mark_price=mark,
                mark_state=state,
            )
            rows = [
                StrategyContribution(
                    **_account(grouped[key], mark),
                    strategy_id=key[0],
                    strategy_version=key[1],
                    instrument_id=instrument_id,
                    quote_currency=currency,
                )
                for key in sorted(grouped)
                if key[2] == instrument_id
            ]
            for field in (
                "fill_count",
                "net_quantity",
                "cash_flow_before_fees_quote",
                "fee_cash_flow_quote",
                "cash_flow_quote",
            ):
                if sum((getattr(row, field) for row in rows), Decimal(0)) != getattr(total, field):
                    raise ValueError("Paper strategy contributions do not reconcile")
            if (
                mark is not None
                and sum((row.marked_pnl_quote for row in rows), Decimal(0))
                != total.marked_pnl_quote
            ):
                raise ValueError("Paper marked contributions do not reconcile")
            strategies.extend(rows)
            totals.append(total)
        gaps = [
            "DECISION_PRICE_BENCHMARK_MISSING",
            "AGENT_CREDIT_EVIDENCE_MISSING",
            "CAUSAL_FACTOR_ATTRIBUTION_MISSING",
            "CANDIDATE_GENOME_BINDING_MISSING",
        ]
        if any(total.mark_state != "AVAILABLE" for total in totals):
            gaps.append("CURRENT_MARK_EVIDENCE_MISSING")
        return StrategyContributionReport(
            "1.0",
            "PAPER",
            now.astimezone(timezone.utc),
            _fingerprint(owners, fills),
            "CUMULATIVE_FROM_EMPTY_INVENTORY",
            "COMMON_PORTFOLIO_LIQUIDATION_MARK_V1",
            tuple(strategies),
            tuple(totals),
            True,
            tuple(gaps),
        )
