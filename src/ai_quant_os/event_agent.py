"""A06 advisory event windows; no risk-policy or order authority."""

from datetime import datetime, timedelta
from dataclasses import asdict, is_dataclass
from decimal import Context, Decimal, localcontext
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, model_validator

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.event_risk import EventMeasurement, event_measurements, paper_event_window
from ai_quant_os.event_sources import EventEvidence, PositionsEvidence
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def _wire(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


class EventAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    event_indices: tuple[Annotated[int, Field(ge=0, le=127)], ...] = Field(max_length=128)
    blackout_recommended: bool | None
    recommended_mode: Literal["NO_NEW_RISK", "OBSERVE_ONLY", "UNASSESSED"]
    filled_net_base_units: Decimal = Field(allow_inf_nan=False)
    pending_buy_base_units: Decimal = Field(ge=0, allow_inf_nan=False)
    pending_sell_base_units: Decimal = Field(ge=0, allow_inf_nan=False)
    has_local_unit_exposure: bool
    may_add_risk: Literal[False] = False


class EventRiskState(AgentEnvelope):
    confidence: Decimal = Field(ge=0, le=0, allow_inf_nan=False)
    agent_id: Literal["A06"] = "A06"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["EventRiskState"] = "EventRiskState"
    policy_version: Literal["uncalibrated-paper-event-windows-v1"] = (
        "uncalibrated-paper-event-windows-v1"
    )
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_authority: Literal["RISK_KERNEL"] = "RISK_KERNEL"
    relevance_scope: Literal["DIRECT_PAIR_CURRENCIES"] = "DIRECT_PAIR_CURRENCIES"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    feed_status: Literal["FRESH", "MISSING", "STALE", "FUTURE"]
    feed_ref: str | None
    observation_as_of: AwareDatetime
    feed_observed_at: AwareDatetime | None
    positions_ref: str
    coverage_complete_for_paper_window: bool
    measurements: tuple[EventMeasurement, ...] = Field(max_length=128)
    assessments: tuple[EventAssessment, ...] = Field(min_length=1, max_length=16)

    @field_serializer("measurements", "assessments", when_used="json")
    def rows_json(self, rows: tuple) -> list:
        return [
            _wire(asdict(row) if is_dataclass(row) else row.model_dump(mode="python"))
            for row in rows
        ]

    @model_validator(mode="after")
    def verified_associations(self) -> "EventRiskState":
        instruments = {row.instrument_id: row for row in PAPER_INSTRUMENTS}
        if self.feed_status != "FRESH" and (
            self.measurements or self.coverage_complete_for_paper_window
        ):
            raise ValueError("Unavailable event feed cannot supply current measurements")
        for event in self.measurements:
            feature = paper_event_window(event.severity, event.seconds_until_event)
            if (
                event.window_seconds != feature.window_seconds
                or event.uncalibrated_score != feature.uncalibrated_score
                or event.blackout_recommended != feature.blackout_recommended
                or (event.kind == "DETECTED" and event.seconds_until_event > 0)
            ):
                raise ValueError("Event measurement disagrees with paper policy")
        if (
            len(self.assessments) != len(instruments)
            or {row.instrument_id for row in self.assessments} != set(instruments)
            or len({row.event_id for row in self.measurements}) != len(self.measurements)
        ):
            raise ValueError("Invalid event assessment coverage")
        for row in self.assessments:
            instrument = instruments[row.instrument_id]
            expected = tuple(
                index
                for index, event in enumerate(self.measurements)
                if {instrument.base_currency, instrument.quote_currency} & set(event.currencies)
            )
            mode = (
                "NO_NEW_RISK"
                if row.blackout_recommended is True
                else "UNASSESSED"
                if row.blackout_recommended is None
                else "OBSERVE_ONLY"
            )
            flags = tuple(self.measurements[index].blackout_recommended for index in expected)
            blackout = (
                True
                if True in flags
                else None
                if self.feed_status != "FRESH"
                or not self.coverage_complete_for_paper_window
                or None in flags
                else False
            )
            if (
                row.event_indices != expected
                or row.recommended_mode != mode
                or row.blackout_recommended is not blackout
            ):
                raise ValueError("Invalid event assessment association or mode")
        return self


async def assess_events(invocation: AgentInvocation) -> EventRiskState:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A06", "0.2.0", "READY"):
        raise ValueError("Event mission context mismatch")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    calendar = EventEvidence.model_validate(inputs["economic_calendar"])
    news = EventEvidence.model_validate(inputs["event_classifications"])
    positions = PositionsEvidence.model_validate(inputs["positions"])
    if calendar != news or calendar.as_of != positions.as_of or calendar.as_of > context.created_at:
        raise ValueError("Event inputs refer to different observations")
    if calendar.status == "FRESH" and calendar.feed.valid_until < context.valid_until:
        raise ValueError("Event output expiry exceeds feed")
    feed = calendar.feed if calendar.status == "FRESH" else None
    measurements = (
        event_measurements(
            feed.economic_calendar + feed.event_classifications, as_of=calendar.as_of
        )
        if feed
        else ()
    )
    covered = bool(
        feed
        and feed.coverage_start <= calendar.as_of - timedelta(seconds=900)
        and feed.coverage_end >= calendar.as_of + timedelta(seconds=900)
    )
    contradictions = [
        "CALIBRATION_UNAVAILABLE",
        "SYNTHETIC_EVENT_FEED",
        "INDIRECT_PORTFOLIO_EVENT_RISK_NOT_ASSESSED",
    ]
    if feed is None:
        contradictions.append("EVENT_FEED_" + calendar.status)
    if not covered:
        contradictions.append("PAPER_WINDOW_COVERAGE_INCOMPLETE")
    assessments = []
    with localcontext(Context(prec=50)):
        for item in positions.instruments:
            relevant = tuple(
                row
                for row in measurements
                if {item.base_currency, item.quote_currency} & set(row.currencies)
            )
            blackout = (
                True
                if any(row.blackout_recommended is True for row in relevant)
                else None
                if feed is None
                or not covered
                or any(row.blackout_recommended is None for row in relevant)
                else False
            )
            filled = sum(
                (
                    row.quantity if row.side == "BUY" else -row.quantity
                    for row in positions.fills
                    if row.instrument_id == item.instrument_id
                ),
                Decimal(0),
            )
            buy = sum(
                (
                    row.remaining_quantity
                    for row in positions.pending
                    if row.instrument_id == item.instrument_id and row.side == "BUY"
                ),
                Decimal(0),
            )
            sell = sum(
                (
                    row.remaining_quantity
                    for row in positions.pending
                    if row.instrument_id == item.instrument_id and row.side == "SELL"
                ),
                Decimal(0),
            )
            assessments.append(
                EventAssessment(
                    instrument_id=item.instrument_id,
                    event_indices=tuple(
                        index
                        for index, event in enumerate(measurements)
                        if {item.base_currency, item.quote_currency} & set(event.currencies)
                    ),
                    blackout_recommended=blackout,
                    recommended_mode="NO_NEW_RISK"
                    if blackout is True
                    else "UNASSESSED"
                    if blackout is None
                    else "OBSERVE_ONLY",
                    filled_net_base_units=filled,
                    pending_buy_base_units=buy,
                    pending_sell_base_units=sell,
                    has_local_unit_exposure=bool(filled or buy or sell),
                )
            )
    return EventRiskState(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(contradictions),
        trace_id=str(uuid4()),
        feed_status=calendar.status,
        feed_ref=calendar.feed_ref,
        positions_ref=positions.positions_ref,
        observation_as_of=calendar.as_of,
        feed_observed_at=calendar.feed.observed_at if calendar.feed else None,
        coverage_complete_for_paper_window=covered,
        measurements=measurements,
        assessments=tuple(assessments),
    )


def event_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A06", "0.2.0").manifest_ref, EventRiskState, assess_events
    )
