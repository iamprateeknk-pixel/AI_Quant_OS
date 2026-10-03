"""Server-owned A04 evidence with complete retained bar lineage."""

from dataclasses import asdict
from decimal import localcontext

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.contracts import _require_aware
from ai_quant_os.currency_strength import PairHistory
from ai_quant_os.market_data import Bar
from ai_quant_os.world_state import WorldState, fingerprint

STRENGTH_INPUT_BYTES = 65536
STRENGTH_CONTEXT_BYTES = 128000


class CurrencyTwinsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world: WorldState

    @model_validator(mode="after")
    def supported_world(self) -> "CurrencyTwinsEvidence":
        world = self.world
        _require_aware(world.as_of)
        _require_aware(world.valid_until)
        if (
            world.source != "PAPER_SIMULATED"
            or world.policy_version != "paper-regime-v1"
            or not 1 <= len(world.instruments) <= 16
            or not 1 <= len(world.currency_twins) <= 16
            or world.valid_until <= world.as_of
            or len({item.instrument_id for item in world.instruments}) != len(world.instruments)
        ):
            raise ValueError("Unsupported strength world")
        currencies = sorted(
            {
                currency
                for item in world.instruments
                for currency in (item.base_currency, item.quote_currency)
            }
        )
        if [twin.currency for twin in world.currency_twins] != currencies:
            raise ValueError("Currency twin coverage mismatch")
        for twin in world.currency_twins:
            related = tuple(
                item
                for item in world.instruments
                if twin.currency in (item.base_currency, item.quote_currency)
            )
            if (
                twin.instrument_ids != tuple(sorted(item.instrument_id for item in related))
                or twin.fresh_quote_count != sum(item.quote_status == "FRESH" for item in related)
                or twin.macro_available
            ):
                raise ValueError("Currency twin provenance mismatch")
        return self


class CurrencyHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str = Field(min_length=1, max_length=128)
    bars: tuple[Bar, ...] = Field(min_length=3, max_length=64)


class CrossPairReturnsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    histories: tuple[CurrencyHistory, ...] = Field(max_length=16)
    excluded_instruments: tuple[str, ...] = Field(max_length=16)


class MacroStrengthEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["NO_MACRO_SOURCE"] = "NO_MACRO_SOURCE"


class RegimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    regime: Literal["TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"]


class RegimeStrengthEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_state_version: str
    basis: Literal["paper-regime-v1"] = "paper-regime-v1"
    observations: tuple[RegimeObservation, ...] = Field(max_length=16)


def verified_pair_histories(
    world: WorldState, evidence: CrossPairReturnsEvidence
) -> tuple[PairHistory, ...]:
    usable = {
        item.instrument_id: item
        for item in world.instruments
        if item.quote_status == item.history_status == "FRESH"
    }
    rows = {row.instrument_id: row for row in evidence.histories}
    excluded = tuple(
        sorted(item.instrument_id for item in world.instruments if item.instrument_id not in usable)
    )
    if (
        evidence.world_state_version != world.world_state_version
        or len(rows) != len(evidence.histories)
        or set(rows) != set(usable)
        or evidence.excluded_instruments != excluded
    ):
        raise ValueError("Strength history coverage mismatch")
    pairs = []
    for identifier in sorted(rows):
        item, bars = usable[identifier], rows[identifier].bars
        reference = "paper-bars:" + fingerprint([asdict(bar) for bar in bars])
        with localcontext() as numeric:
            numeric.prec = 28  # Match the retained paper-regime-v1 world calculation.
            declared_return = (bars[-1].close / bars[0].close - 1) * 10000
        if (
            reference not in item.evidence_refs
            or len(bars) != item.bar_count
            or any(bar.instrument_id != identifier or bar.observed_at > world.as_of for bar in bars)
            or item.return_bps is None
            or declared_return != item.return_bps
        ):
            raise ValueError("Strength history provenance mismatch")
        pairs.append(
            PairHistory(
                identifier,
                item.base_currency,
                item.quote_currency,
                tuple(bar.observed_at for bar in bars),
                tuple(bar.close for bar in bars),
                reference,
            )
        )
    return tuple(pairs)


def currency_strength_sources(
    world: WorldState, histories: dict[str, tuple[Bar, ...]]
) -> dict[str, SourceBinding]:
    twins = CurrencyTwinsEvidence(world=world)
    if set(histories) - {item.instrument_id for item in world.instruments}:
        raise ValueError("Unknown strength history instrument")
    usable = {
        item.instrument_id
        for item in world.instruments
        if item.quote_status == item.history_status == "FRESH"
    }
    returns = CrossPairReturnsEvidence(
        world_state_version=world.world_state_version,
        histories=tuple(
            CurrencyHistory(instrument_id=identifier, bars=histories.get(identifier, ()))
            for identifier in sorted(usable)
        ),
        excluded_instruments=tuple(
            sorted(
                item.instrument_id for item in world.instruments if item.instrument_id not in usable
            )
        ),
    )
    verified_pair_histories(world, returns)
    payloads = {
        "currency_twins": (twins, "WORLD_STATE_READ"),
        "cross_pair_returns": (returns, "HISTORY_READ"),
        "macro_state": (
            MacroStrengthEvidence(world_state_version=world.world_state_version),
            "MACRO_READ",
        ),
        "regime_state": (
            RegimeStrengthEvidence(
                world_state_version=world.world_state_version,
                observations=tuple(
                    RegimeObservation(instrument_id=item.instrument_id, regime=item.regime)
                    for item in world.instruments
                ),
            ),
            "WORLD_STATE_READ",
        ),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        record = EvidenceRecord(
            source_id=f"paper-strength:{name}:{world.world_state_version}",
            as_of=world.as_of,
            valid_until=world.valid_until,
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
