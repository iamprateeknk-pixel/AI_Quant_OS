"""A06 snapshots from an audited event feed and one atomic paper unit ledger."""

from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.currency_portfolio import CurrencyInstrument, CurrencyPendingOrder
from ai_quant_os.event_feed import EventFeed, feed_status
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.portfolio import PaperFill
from ai_quant_os.world_state import fingerprint

EVENT_INPUT_BYTES = 65536
EVENT_CONTEXT_BYTES = 160000


class EventEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    feed_ref: str | None
    status: Literal["FRESH", "MISSING", "STALE", "FUTURE"]
    feed: EventFeed | None

    @model_validator(mode="after")
    def verified(self) -> "EventEvidence":
        if self.status != feed_status(self.feed, self.as_of) or self.feed_ref != (
            self.feed.content_ref if self.feed else None
        ):
            raise ValueError("Event evidence status or identity mismatch")
        return self


class PositionsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    positions_ref: str
    source: Literal["LOCAL_PAPER_LEDGER"] = "LOCAL_PAPER_LEDGER"
    instruments: tuple[CurrencyInstrument, ...]
    fills: tuple[PaperFill, ...] = Field(max_length=512)
    pending: tuple[CurrencyPendingOrder, ...] = Field(max_length=512)

    def material(self) -> dict:
        return {
            "as_of": self.as_of,
            "instruments": [asdict(row) for row in self.instruments],
            "fills": [asdict(row) for row in self.fills],
            "pending": [asdict(row) for row in self.pending],
        }

    @model_validator(mode="after")
    def verified(self) -> "PositionsEvidence":
        identifiers = {row.instrument_id for row in PAPER_INSTRUMENTS}
        quantities = tuple(row.quantity for row in self.fills) + tuple(
            row.remaining_quantity for row in self.pending
        )
        if (
            self.instruments != PAPER_INSTRUMENTS
            or self.positions_ref != "paper-event-positions:" + fingerprint(self.material())
            or len({row.fill_id for row in self.fills}) != len(self.fills)
            or len({row.order_id for row in self.pending}) != len(self.pending)
            or any(
                row.instrument_id not in identifiers or row.observed_at > self.as_of
                for row in self.fills
            )
            or any(row.instrument_id not in identifiers for row in self.pending)
            or any(
                not value.is_finite()
                or not 0 < value <= Decimal("1e12")
                or len(value.as_tuple().digits) > 64
                or not -12 <= value.as_tuple().exponent <= 12
                for value in quantities
            )
        ):
            raise ValueError("Event positions evidence mismatch or budget exceeded")
        return self


def event_sources(
    feed: EventFeed | None,
    fills: tuple[PaperFill, ...],
    pending: tuple[CurrencyPendingOrder, ...],
    *,
    now: datetime,
) -> dict[str, SourceBinding]:
    evidence = EventEvidence(
        as_of=now,
        status=feed_status(feed, now),
        feed=feed,
        feed_ref=feed.content_ref if feed else None,
    )
    material = {
        "as_of": now,
        "instruments": [asdict(row) for row in PAPER_INSTRUMENTS],
        "fills": [asdict(row) for row in fills],
        "pending": [asdict(row) for row in pending],
    }
    positions = PositionsEvidence(
        as_of=now,
        positions_ref="paper-event-positions:" + fingerprint(material),
        instruments=PAPER_INSTRUMENTS,
        fills=fills,
        pending=pending,
    )
    expiry = (
        min(now + timedelta(seconds=5), feed.valid_until)
        if evidence.status == "FRESH"
        else now + timedelta(seconds=5)
    )
    sources = {}
    for name, payload, tool in (
        ("economic_calendar", evidence, "MACRO_READ"),
        ("event_classifications", evidence, "WORLD_STATE_READ"),
        ("positions", positions, "PORTFOLIO_READ"),
    ):
        record = EvidenceRecord(
            source_id=f"paper-event:{name}:{fingerprint(payload.model_dump(mode='json'))}",
            as_of=now,
            valid_until=expiry,
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources
