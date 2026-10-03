"""Versioned A05 paper evidence and checked quote/bar/spread lineage."""

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.contracts import _require_aware
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.market_data import Bar
from ai_quant_os.volatility_features import volatility_features
from ai_quant_os.world_state import WorldState, fingerprint

LIQUIDITY_INPUT_BYTES = 65536
LIQUIDITY_CONTEXT_BYTES = 160000


def require_liquidity_quote(quote: MarketSnapshot) -> None:
    if any(
        not Decimal("1e-12") <= value <= Decimal("1e12") or len(value.as_tuple().digits) > 64
        for value in (quote.bid, quote.ask)
    ):
        raise ValueError("Liquidity quote exceeds price representation budget")


class DisplayedDepth(BaseModel):
    """Operator supplied simulated L1, bound to one exact quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    quote_ref: str
    observed_at: datetime
    bid_size: Decimal = Field(ge=0, le=Decimal("1e12"), allow_inf_nan=False)
    ask_size: Decimal = Field(ge=0, le=Decimal("1e12"), allow_inf_nan=False)
    units: Literal["BASE_CURRENCY_UNITS"] = "BASE_CURRENCY_UNITS"
    provenance: Literal["PAPER_OPERATOR_SYNTHETIC_L1"] = "PAPER_OPERATOR_SYNTHETIC_L1"

    @field_validator("bid_size", "ask_size")
    @classmethod
    def bounded_size(cls, value: Decimal) -> Decimal:
        if len(value.as_tuple().digits) > 64 or not -12 <= value.as_tuple().exponent <= 12:
            raise ValueError("Displayed sizes exceed decimal representation budget")
        return value


class QuotesEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world: WorldState
    quotes: tuple[MarketSnapshot, ...] = Field(max_length=16)
    displayed_depth: tuple[DisplayedDepth, ...] = Field(default=(), max_length=16)
    quote_max_age_seconds: Literal[5] = 5
    bar_max_age_seconds: Literal[60] = 60


class BarHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    bars: tuple[Bar, ...] = Field(min_length=1, max_length=64)


class BarsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    histories: tuple[BarHistory, ...] = Field(max_length=16)


class SpreadObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    absolute_spread: Decimal = Field(ge=0, allow_inf_nan=False)
    spread_bps: Decimal = Field(ge=0, allow_inf_nan=False)


class SpreadsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    observations: tuple[SpreadObservation, ...] = Field(max_length=16)


class SessionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    clock_basis: Literal["UTC"] = "UTC"


class BrokerTelemetryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["NO_EXECUTION_TELEMETRY"] = "NO_EXECUTION_TELEMETRY"
    depth_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"


def verified_liquidity_inputs(
    quote_evidence: QuotesEvidence, bar_evidence: BarsEvidence, spread_evidence: SpreadsEvidence
) -> dict[str, tuple[Bar, ...]]:
    world = quote_evidence.world
    _require_aware(world.as_of)
    _require_aware(world.valid_until)
    quotes = {row.instrument_id: row for row in quote_evidence.quotes}
    histories = {row.instrument_id: row.bars for row in bar_evidence.histories}
    spreads = {row.instrument_id: row for row in spread_evidence.observations}
    identifiers = {item.instrument_id for item in world.instruments}
    depth = {row.instrument_id: row for row in quote_evidence.displayed_depth}
    if (
        world.source != "PAPER_SIMULATED"
        or world.policy_version != "paper-regime-v1"
        or not 1 <= len(world.instruments) <= 16
        or len(identifiers) != len(world.instruments)
        or world.valid_until <= world.as_of
        or len(quotes) != len(quote_evidence.quotes)
        or len(histories) != len(bar_evidence.histories)
        or len(spreads) != len(spread_evidence.observations)
        or len(depth) != len(quote_evidence.displayed_depth)
        or set(depth) - set(quotes)
        or (set(quotes) | set(histories)) - identifiers
        or set(spreads) != set(quotes)
        or bar_evidence.world_state_version != world.world_state_version
        or spread_evidence.world_state_version != world.world_state_version
    ):
        raise ValueError("Liquidity source coverage mismatch")
    for identifier, row in depth.items():
        quote = quotes[identifier]
        _require_aware(row.observed_at)
        if (
            row.quote_ref != "paper-quote:" + fingerprint(asdict(quote))
            or row.observed_at != quote.observed_at
        ):
            raise ValueError("Displayed depth quote provenance mismatch")
    for item in world.instruments:
        quote, bars = quotes.get(item.instrument_id), histories.get(item.instrument_id, ())
        if quote is None:
            if (
                item.quote_status != "MISSING"
                or item.mid_price is not None
                or item.spread_bps is not None
            ):
                raise ValueError("Missing liquidity quote provenance")
        else:
            require_liquidity_quote(quote)
            age = (world.as_of - quote.observed_at).total_seconds()
            expected = (
                "UNTRUSTED"
                if not quote.trusted
                else "FUTURE"
                if age < 0
                else "STALE"
                if age > 5
                else "FRESH"
            )
            with localcontext() as numeric:
                numeric.prec = 28
                mid = (quote.bid + quote.ask) / 2
                absolute_spread = quote.spread
                spread_bps = absolute_spread / mid * 10000
            observed = spreads[item.instrument_id]
            if (
                "paper-quote:" + fingerprint(asdict(quote)) not in item.evidence_refs
                or item.quote_status != expected
                or item.mid_price != mid
                or item.spread_bps != spread_bps
                or observed.absolute_spread != absolute_spread
                or observed.spread_bps != spread_bps
            ):
                raise ValueError("Liquidity quote/spread provenance mismatch")
        if len(bars) != item.bar_count or any(
            bar.instrument_id != item.instrument_id or bar.observed_at > world.as_of for bar in bars
        ):
            raise ValueError("Liquidity bar coverage mismatch")
        volatility_features(bars)
        if (
            bars
            and "paper-bars:" + fingerprint([asdict(bar) for bar in bars]) not in item.evidence_refs
        ):
            raise ValueError("Liquidity bar provenance mismatch")
        history_status = (
            "STALE"
            if bars and (world.as_of - bars[-1].observed_at).total_seconds() > 60
            else "FRESH"
            if len(bars) >= 3
            else "INSUFFICIENT"
        )
        if item.history_status != history_status:
            raise ValueError("Liquidity history freshness mismatch")
    return histories


def liquidity_sources(
    world: WorldState,
    histories: dict[str, tuple[Bar, ...]],
    quotes: tuple[MarketSnapshot, ...],
    displayed_depth: tuple[DisplayedDepth, ...] = (),
) -> dict[str, SourceBinding]:
    for quote in quotes:
        require_liquidity_quote(quote)
    quote_refs = {
        quote.instrument_id: "paper-quote:" + fingerprint(asdict(quote)) for quote in quotes
    }
    quote_evidence = QuotesEvidence(
        world=world,
        quotes=quotes,
        displayed_depth=tuple(
            row for row in displayed_depth if quote_refs.get(row.instrument_id) == row.quote_ref
        ),
    )
    bar_evidence = BarsEvidence(
        world_state_version=world.world_state_version,
        histories=tuple(
            BarHistory(instrument_id=identifier, bars=bars)
            for identifier, bars in sorted(histories.items())
            if bars
        ),
    )
    with localcontext() as numeric:
        numeric.prec = 28
        spreads = SpreadsEvidence(
            world_state_version=world.world_state_version,
            observations=tuple(
                SpreadObservation(
                    instrument_id=quote.instrument_id,
                    absolute_spread=quote.spread,
                    spread_bps=quote.spread / ((quote.bid + quote.ask) / 2) * 10000,
                )
                for quote in quotes
            ),
        )
    verified_liquidity_inputs(quote_evidence, bar_evidence, spreads)
    payloads = {
        "quotes": (quote_evidence, "WORLD_STATE_READ"),
        "bars": (bar_evidence, "HISTORY_READ"),
        "spreads": (spreads, "WORLD_STATE_READ"),
        "session_state": (
            SessionEvidence(world_state_version=world.world_state_version),
            "WORLD_STATE_READ",
        ),
        "broker_telemetry": (
            BrokerTelemetryEvidence(world_state_version=world.world_state_version),
            "HEALTH_READ",
        ),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        record = EvidenceRecord(
            source_id=f"paper-liquidity:{name}:{world.world_state_version}",
            as_of=world.as_of,
            valid_until=world.valid_until,
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
