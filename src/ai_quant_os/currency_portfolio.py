from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.portfolio import PaperFill


def _require_currency(currency: str) -> None:
    if re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise ValueError("Currency must be a three-letter uppercase code")


@dataclass(frozen=True)
class CurrencyInstrument:
    instrument_id: str
    base_currency: str
    quote_currency: str

    def __post_init__(self) -> None:
        _require_currency(self.base_currency)
        _require_currency(self.quote_currency)
        if not self.instrument_id or self.base_currency == self.quote_currency:
            raise ValueError("Distinct instrument currencies and identifier are required")


@dataclass(frozen=True)
class CurrencyPendingOrder:
    order_id: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    remaining_quantity: Decimal
    max_slippage: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.order_id or not self.instrument_id or self.side not in {"BUY", "SELL"}:
            raise ValueError("Invalid pending order")
        if not self.remaining_quantity.is_finite() or self.remaining_quantity <= 0:
            raise ValueError("Pending quantity must be positive and finite")
        if not self.max_slippage.is_finite() or self.max_slippage < 0:
            raise ValueError("Pending slippage must be nonnegative and finite")


@dataclass(frozen=True)
class FxRate:
    base_currency: str
    quote_currency: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    trusted: bool

    def __post_init__(self) -> None:
        _require_currency(self.base_currency)
        _require_currency(self.quote_currency)
        _require_aware(self.observed_at)
        if self.base_currency == self.quote_currency:
            raise ValueError("FX currencies must differ")
        if (
            not self.bid.is_finite()
            or not self.ask.is_finite()
            or self.bid <= 0
            or self.ask < self.bid
        ):
            raise ValueError("Invalid FX bid/ask")


@dataclass(frozen=True)
class CurrencyExposure:
    currency: str
    filled_balance: Decimal
    minimum_balance: Decimal | None
    maximum_balance: Decimal | None
    marked_balance_reporting: Decimal | None
    worst_case_exposure_reporting: Decimal | None
    issue: str | None


@dataclass(frozen=True)
class CurrencyPortfolioSnapshot:
    schema_version: str
    snapshot_id: str
    reporting_currency: str
    currencies: tuple[CurrencyExposure, ...]
    marked_pnl_reporting: Decimal | None
    gross_worst_case_exposure_reporting: Decimal | None
    issues: tuple[str, ...]

    @property
    def valuation_available(self) -> bool:
        return not self.issues


class _RateUnavailable(ValueError):
    pass


class _FxRates:
    def __init__(self, rates: tuple[FxRate, ...], now: datetime, max_age: int) -> None:
        self._rates: dict[tuple[str, str], FxRate] = {}
        for rate in rates:
            pair = (rate.base_currency, rate.quote_currency)
            if pair in self._rates or pair[::-1] in self._rates:
                raise ValueError("Duplicate or ambiguous FX conversion pair")
            self._rates[pair] = rate
        self._now = now
        self._max_age = max_age

    def prices(self, base: str, quote: str) -> tuple[Decimal, Decimal]:
        if base == quote:
            return Decimal(1), Decimal(1)
        direct = self._rates.get((base, quote))
        rate = direct or self._rates.get((quote, base))
        if rate is None:
            raise _RateUnavailable("MISSING_FX_RATE")
        if not rate.trusted:
            raise _RateUnavailable("UNTRUSTED_FX_RATE")
        if not 0 <= (self._now - rate.observed_at).total_seconds() <= self._max_age:
            raise _RateUnavailable("STALE_FX_RATE")
        if direct is not None:
            return rate.bid, rate.ask
        return Decimal(1) / rate.ask, Decimal(1) / rate.bid


def summarize_currency_portfolio(
    fills: tuple[PaperFill, ...],
    *,
    pending: tuple[CurrencyPendingOrder, ...],
    instruments: tuple[CurrencyInstrument, ...],
    rates: tuple[FxRate, ...],
    reporting_currency: str,
    now: datetime,
    max_rate_age_seconds: int,
) -> CurrencyPortfolioSnapshot:
    _require_currency(reporting_currency)
    _require_aware(now)
    if max_rate_age_seconds <= 0:
        raise ValueError("FX freshness threshold must be positive")
    catalog = {item.instrument_id: item for item in instruments}
    if len(catalog) != len(instruments):
        raise ValueError("Duplicate instrument metadata")
    if len({fill.fill_id for fill in fills}) != len(fills):
        raise ValueError("Duplicate fill identifier")
    if len({order.order_id for order in pending}) != len(pending):
        raise ValueError("Duplicate pending order identifier")
    fx = _FxRates(rates, now, max_rate_age_seconds)
    balances: dict[str, Decimal] = {}
    negative_deltas: dict[str, Decimal] = {}
    positive_deltas: dict[str, Decimal] = {}
    unknown_bounds: dict[str, str] = {}

    def instrument(identifier: str) -> CurrencyInstrument:
        if identifier not in catalog:
            raise ValueError(f"Unknown instrument currency metadata: {identifier}")
        return catalog[identifier]

    def add_balance(currency: str, quantity: Decimal) -> None:
        balances[currency] = balances.get(currency, Decimal(0)) + quantity

    def add_pending(currency: str, delta: Decimal) -> None:
        add_balance(currency, Decimal(0))
        target = negative_deltas if delta < 0 else positive_deltas
        target[currency] = target.get(currency, Decimal(0)) + delta

    for fill in fills:
        item = instrument(fill.instrument_id)
        if fill.observed_at > now:
            raise ValueError("Fill timestamp is after valuation time")
        signed = fill.quantity if fill.side == "BUY" else -fill.quantity
        add_balance(item.base_currency, signed)
        add_balance(item.quote_currency, -signed * fill.price - fill.fee)

    for order in pending:
        item = instrument(order.instrument_id)
        signed = order.remaining_quantity if order.side == "BUY" else -order.remaining_quantity
        add_pending(item.base_currency, signed)
        add_balance(item.quote_currency, Decimal(0))
        try:
            bid, ask = fx.prices(item.base_currency, item.quote_currency)
            price_bound = (ask if signed > 0 else bid) + order.max_slippage
            add_pending(item.quote_currency, -signed * price_bound)
        except _RateUnavailable as error:
            unknown_bounds[item.quote_currency] = str(error)

    exposures: list[CurrencyExposure] = []
    for currency, balance in sorted(balances.items()):
        minimum = balance + negative_deltas.get(currency, Decimal(0))
        maximum = balance + positive_deltas.get(currency, Decimal(0))
        marked: Decimal | None = None
        worst_case: Decimal | None = None
        issue = unknown_bounds.get(currency)
        try:
            if balance == minimum == maximum == 0:
                marked, worst_case = Decimal(0), Decimal(0)
            else:
                bid, ask = fx.prices(currency, reporting_currency)
                marked = balance * (bid if balance >= 0 else ask)
                worst_case = max(abs(minimum), abs(maximum)) * ask
        except _RateUnavailable as error:
            issue = issue or str(error)
        if currency in unknown_bounds:
            minimum, maximum, worst_case = None, None, None
        exposures.append(
            CurrencyExposure(currency, balance, minimum, maximum, marked, worst_case, issue)
        )

    issues = tuple(sorted({item.issue for item in exposures if item.issue is not None}))
    fingerprint = json.dumps(
        {
            "fills": [asdict(fill) for fill in sorted(fills, key=lambda item: item.fill_id)],
            "pending": [asdict(order) for order in sorted(pending, key=lambda item: item.order_id)],
            "instruments": [
                asdict(item) for item in sorted(instruments, key=lambda item: item.instrument_id)
            ],
            "rates": [
                asdict(rate)
                for rate in sorted(
                    rates, key=lambda item: (item.base_currency, item.quote_currency)
                )
            ],
            "reporting_currency": reporting_currency,
            "now": now.isoformat(),
            "max_rate_age_seconds": max_rate_age_seconds,
        },
        sort_keys=True,
        default=str,
    ).encode()
    return CurrencyPortfolioSnapshot(
        schema_version="1.0",
        snapshot_id=f"currency:{sha256(fingerprint).hexdigest()}",
        reporting_currency=reporting_currency,
        currencies=tuple(exposures),
        marked_pnl_reporting=(
            sum((item.marked_balance_reporting for item in exposures), Decimal(0))
            if not issues
            else None
        ),
        gross_worst_case_exposure_reporting=(
            sum((item.worst_case_exposure_reporting for item in exposures), Decimal(0))
            if not issues
            else None
        ),
        issues=issues,
    )
