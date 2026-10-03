"""Bounded synthetic calendar/classification snapshot with immutable identity."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.event_risk import EventObservation, event_measurements


class EventFeed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    provenance: Literal["PAPER_OPERATOR_SYNTHETIC_EVENT_FEED"] = (
        "PAPER_OPERATOR_SYNTHETIC_EVENT_FEED"
    )
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime
    economic_calendar: tuple[EventObservation, ...] = Field(default=(), max_length=128)
    event_classifications: tuple[EventObservation, ...] = Field(default=(), max_length=128)

    @field_validator("observed_at", "valid_until", "coverage_start", "coverage_end")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(timezone.utc)
        except OverflowError as error:
            raise ValueError("Event feed timestamp is outside UTC range") from error

    @model_validator(mode="after")
    def bounded_coverage(self) -> "EventFeed":
        events = self.economic_calendar + self.event_classifications
        if (
            not timedelta(0) < self.valid_until - self.observed_at <= timedelta(seconds=300)
            or not self.coverage_start <= self.observed_at < self.coverage_end
            or self.coverage_end - self.coverage_start > timedelta(days=7)
            or any(row.kind != "SCHEDULED" for row in self.economic_calendar)
            or any(row.kind != "DETECTED" for row in self.event_classifications)
            or any(
                not self.coverage_start <= row.occurs_at <= self.coverage_end
                or set(row.currencies) - {"EUR", "GBP", "JPY", "USD"}
                for row in events
            )
        ):
            raise ValueError("Invalid bounded event feed coverage")
        event_measurements(events, as_of=self.observed_at)
        return self

    @property
    def content_ref(self) -> str:
        return "paper-events:" + sha256(self.model_dump_json().encode()).hexdigest()


def feed_status(feed: EventFeed | None, now: datetime) -> str:
    return (
        "MISSING"
        if feed is None
        else "FUTURE"
        if now < feed.observed_at
        else "STALE"
        if now >= feed.valid_until
        else "FRESH"
    )
