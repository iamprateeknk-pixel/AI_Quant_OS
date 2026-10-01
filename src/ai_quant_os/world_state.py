"""Deterministic advisory FX state from bounded paper market evidence."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
from threading import Lock

from ai_quant_os.contracts import _require_aware
from ai_quant_os.currency_portfolio import CurrencyInstrument
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar, BarDataset


def fingerprint(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError("Unsupported world-state evidence")

    return sha256(
        json.dumps(value, default=encode, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class InstrumentWorldState:
    instrument_id: str
    base_currency: str
    quote_currency: str
    quote_status: str
    mid_price: Decimal | None
    spread_bps: Decimal | None
    history_status: str
    bar_count: int
    return_bps: Decimal | None
    realized_volatility_bps: Decimal | None
    regime: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class CurrencyTwin:
    currency: str
    instrument_ids: tuple[str, ...]
    fresh_quote_count: int
    macro_available: bool = False


@dataclass(frozen=True)
class WorldState:
    schema_version: str
    world_state_version: str
    as_of: datetime
    valid_until: datetime
    status: str
    instruments: tuple[InstrumentWorldState, ...]
    currency_twins: tuple[CurrencyTwin, ...]
    policy_version: str = "paper-regime-v1"
    source: str = "PAPER_SIMULATED"


class WorldStateEngine:
    def __init__(
        self,
        instruments: tuple[CurrencyInstrument, ...],
        *,
        history_limit: int = 64,
        max_quote_age_seconds: int = 5,
        max_bar_age_seconds: int = 60,
    ) -> None:
        if not 3 <= history_limit <= 4096 or max_quote_age_seconds <= 0 or max_bar_age_seconds <= 0:
            raise ValueError("Invalid world-state history or freshness policy")
        if len({item.instrument_id for item in instruments}) != len(instruments):
            raise ValueError("Duplicate world-state instrument")
        self.instruments = instruments
        self.history_limit = history_limit
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_bar_age_seconds = max_bar_age_seconds
        self._bars: dict[str, tuple[Bar, ...]] = {}
        self._lock = Lock()

    def histories(self) -> dict[str, tuple[Bar, ...]]:
        """Return immutable bounded bars; consumers must match snapshot evidence hashes."""
        with self._lock:
            return self._bars.copy()

    def record_bars(self, bars: tuple[Bar, ...], *, now: datetime) -> int:
        return self.record_bars_with_health(bars, now=now)[0]

    def record_bars_with_health(self, bars: tuple[Bar, ...], *, now: datetime) -> tuple[int, int]:
        _require_aware(now)
        identifiers = {item.instrument_id for item in self.instruments}
        if any(item.instrument_id not in identifiers for item in bars):
            raise ValueError("Unknown paper world-state instrument")
        if any(item.observed_at > now for item in bars):
            raise ValueError("World-state bars cannot be future-dated")
        incoming = BarDataset(bars).bars
        with self._lock:
            seen = {
                (bar.instrument_id, bar.observed_at) for rows in self._bars.values() for bar in rows
            }
            duplicate_rows = 0
            for bar in bars:
                key = (bar.instrument_id, bar.observed_at)
                duplicate_rows += key in seen
                seen.add(key)
            updated = self._bars.copy()
            inserted = 0
            for identifier in sorted({bar.instrument_id for bar in incoming}):
                original = self._bars.get(identifier, ())
                combined = BarDataset(
                    original + tuple(bar for bar in incoming if bar.instrument_id == identifier)
                )
                retained = combined.bars[-self.history_limit :]
                original_keys = {bar.observed_at for bar in original}
                inserted += sum(bar.observed_at not in original_keys for bar in retained)
                updated[identifier] = retained
            self._bars = updated
            return inserted, duplicate_rows

    def _instrument_state(
        self,
        instrument: CurrencyInstrument,
        quote: MarketSnapshot | None,
        bars: tuple[Bar, ...],
        now: datetime,
    ) -> InstrumentWorldState:
        quote_status = "MISSING"
        mid = spread = None
        refs: list[str] = []
        if quote is not None:
            age = (now - quote.observed_at).total_seconds()
            quote_status = (
                "UNTRUSTED"
                if not quote.trusted
                else "FUTURE"
                if age < 0
                else "STALE"
                if age > self.max_quote_age_seconds
                else "FRESH"
            )
            mid = (quote.bid + quote.ask) / 2
            spread = (quote.ask - quote.bid) / mid * 10000
            refs.append("paper-quote:" + fingerprint(asdict(quote)))
        history_status = "INSUFFICIENT"
        change = volatility = None
        regime = "UNKNOWN"
        if bars:
            refs.append("paper-bars:" + fingerprint([asdict(bar) for bar in bars]))
            if (now - bars[-1].observed_at).total_seconds() > self.max_bar_age_seconds:
                history_status = "STALE"
            elif len(bars) >= 3:
                history_status = "FRESH"
                change = (bars[-1].close / bars[0].close - 1) * 10000
                returns = tuple(
                    (right.close / left.close - 1) * 10000 for left, right in zip(bars, bars[1:])
                )
                mean = sum(returns) / len(returns)
                volatility = (sum((value - mean) ** 2 for value in returns) / len(returns)).sqrt()
                if quote_status == "FRESH":
                    regime = (
                        "HIGH_VOLATILITY"
                        if volatility >= 25
                        else "TRENDING"
                        if abs(change) >= 20
                        else "RANGING"
                    )
        return InstrumentWorldState(
            instrument.instrument_id,
            instrument.base_currency,
            instrument.quote_currency,
            quote_status,
            mid,
            spread,
            history_status,
            len(bars),
            change,
            volatility,
            regime,
            tuple(refs),
        )

    def snapshot(self, quotes: tuple[MarketSnapshot, ...], *, now: datetime) -> WorldState:
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        if len({quote.instrument_id for quote in quotes}) != len(quotes):
            raise ValueError("Duplicate world-state quote")
        by_instrument = {quote.instrument_id: quote for quote in quotes}
        with self._lock:
            histories = self._bars.copy()
        with localcontext() as context:
            context.prec = 28
            states = tuple(
                self._instrument_state(
                    item,
                    by_instrument.get(item.instrument_id),
                    tuple(
                        bar
                        for bar in histories.get(item.instrument_id, ())
                        if bar.observed_at <= now
                    ),
                    now,
                )
                for item in self.instruments
            )
        currencies = sorted(
            {
                currency
                for item in self.instruments
                for currency in (item.base_currency, item.quote_currency)
            }
        )
        twins = tuple(
            CurrencyTwin(
                currency,
                tuple(
                    sorted(
                        item.instrument_id
                        for item in states
                        if currency in (item.base_currency, item.quote_currency)
                    )
                ),
                sum(
                    item.quote_status == "FRESH"
                    for item in states
                    if currency in (item.base_currency, item.quote_currency)
                ),
            )
            for currency in currencies
        )
        status = (
            "EMPTY"
            if all(item.quote_status == "MISSING" for item in states)
            else ("READY" if all(item.regime != "UNKNOWN" for item in states) else "DEGRADED")
        )
        expiries = [now + timedelta(seconds=self.max_quote_age_seconds)]
        for item in states:
            if item.quote_status == "FRESH":
                expiries.append(
                    by_instrument[item.instrument_id].observed_at
                    + timedelta(seconds=self.max_quote_age_seconds)
                )
            if item.history_status == "FRESH":
                visible = tuple(
                    bar for bar in histories.get(item.instrument_id, ()) if bar.observed_at <= now
                )
                expiries.append(
                    visible[-1].observed_at + timedelta(seconds=self.max_bar_age_seconds)
                )
        valid_until = min(expiries)
        payload = {
            "schema_version": "1.0",
            "policy_version": "paper-regime-v1",
            "as_of": now,
            "valid_until": valid_until,
            "instruments": [asdict(item) for item in states],
            "currency_twins": [asdict(item) for item in twins],
            "max_quote_age_seconds": self.max_quote_age_seconds,
            "max_bar_age_seconds": self.max_bar_age_seconds,
            "history_limit": self.history_limit,
        }
        return WorldState(
            "1.0", "world:" + fingerprint(payload), now, valid_until, status, states, twins
        )
