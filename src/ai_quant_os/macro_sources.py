"""A03 checked world, dated rate archive and explicit event/release evidence."""

from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.event_feed import EventFeed, feed_status
from ai_quant_os.event_sources import EventEvidence
from ai_quant_os.macro_drivers import rate_receipt_status
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.policy_rates import RateCapture
from ai_quant_os.rate_archive import RateArchive
from ai_quant_os.world_state import WorldState, fingerprint

MACRO_INPUT_BYTES = 65536
MACRO_CONTEXT_BYTES = 160000


def latest_rate_capture(path: Path | None) -> RateCapture | None:
    if path is None or not path.exists():
        return None
    with RateArchive(path) as archive:
        rows = archive.list()
        return rows[-1] if rows else None


class MacroObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")


class MissingMacroEvidence(MacroObservation):
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["NO_MACRO_RELEASE_OR_CENTRAL_BANK_TEXT"] = (
        "NO_MACRO_RELEASE_OR_CENTRAL_BANK_TEXT"
    )


class MacroRatesEvidence(MacroObservation):
    capture: RateCapture | None
    capture_ref: str | None
    receipt_status: Literal["MISSING", "FUTURE_RECEIPT", "OLD_RECEIPT", "RECENT_RECEIPT"]
    yields_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"

    @model_validator(mode="after")
    def checked(self) -> "MacroRatesEvidence":
        if self.capture_ref != (
            self.capture.capture_ref if self.capture else None
        ) or self.receipt_status != rate_receipt_status(
            self.capture.received_at if self.capture else None, self.as_of
        ):
            raise ValueError("Macro rate receipt identity or status mismatch")
        return self


class MacroEventsEvidence(MacroObservation):
    evidence: EventEvidence

    @model_validator(mode="after")
    def checked(self) -> "MacroEventsEvidence":
        if self.evidence.as_of != self.as_of:
            raise ValueError("Macro event observation mismatch")
        return self


class MacroWorldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world: WorldState

    @model_validator(mode="after")
    def checked(self) -> "MacroWorldEvidence":
        world = self.world
        expected_status = (
            "EMPTY"
            if all(row.quote_status == "MISSING" for row in world.instruments)
            else "READY"
            if all(row.regime != "UNKNOWN" for row in world.instruments)
            else "DEGRADED"
        )
        if (
            world.schema_version != "1.0"
            or world.source != "PAPER_SIMULATED"
            or world.policy_version != "paper-regime-v1"
            or world.valid_until <= world.as_of
            or world.status != expected_status
            or len(world.instruments) != 4
            or tuple(
                (row.instrument_id, row.base_currency, row.quote_currency)
                for row in world.instruments
            )
            != tuple(
                (row.instrument_id, row.base_currency, row.quote_currency)
                for row in PAPER_INSTRUMENTS
            )
        ):
            raise ValueError("Unsupported macro paper world")
        payload = {
            "schema_version": world.schema_version,
            "policy_version": world.policy_version,
            "as_of": world.as_of,
            "valid_until": world.valid_until,
            "instruments": [asdict(row) for row in world.instruments],
            "currency_twins": [asdict(row) for row in world.currency_twins],
            "max_quote_age_seconds": 5,
            "max_bar_age_seconds": 60,
            "history_limit": 64,
        }
        if (
            world.world_state_version != "world:" + fingerprint(payload)
            or tuple(row.currency for row in world.currency_twins) != ("EUR", "GBP", "JPY", "USD")
            or any(row.macro_available for row in world.currency_twins)
        ):
            raise ValueError("Macro world fingerprint or currency coverage mismatch")
        return self


def macro_sources(
    world: WorldState, capture: RateCapture | None, feed: EventFeed | None
) -> dict[str, SourceBinding]:
    world_evidence = MacroWorldEvidence(world=world)
    common = {"as_of": world.as_of, "world_state_version": world.world_state_version}
    rates = MacroRatesEvidence(
        **common,
        capture=capture,
        capture_ref=capture.capture_ref if capture else None,
        receipt_status=rate_receipt_status(capture.received_at if capture else None, world.as_of),
    )
    events = MacroEventsEvidence(
        **common,
        evidence=EventEvidence(
            as_of=world.as_of,
            feed=feed,
            feed_ref=feed.content_ref if feed else None,
            status=feed_status(feed, world.as_of),
        ),
    )
    expiry = world.valid_until
    if rates.receipt_status == "RECENT_RECEIPT":
        expiry = min(expiry, capture.received_at + timedelta(seconds=300))
    if events.evidence.status == "FRESH":
        expiry = min(expiry, feed.valid_until)
    sources = {}
    for name, payload, tool in [
        ("macro_feed", MissingMacroEvidence(**common), "MACRO_READ"),
        ("event_feed", events, "MACRO_READ"),
        ("rates_yields", rates, "MACRO_READ"),
        ("world_state", world_evidence, "WORLD_STATE_READ"),
    ]:
        record = EvidenceRecord(
            source_id=f"paper-macro:{name}:{fingerprint(payload.model_dump(mode='json'))}",
            as_of=world.as_of,
            valid_until=expiry,
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
