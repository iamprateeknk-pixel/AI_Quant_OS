"""Partial A03 dated drivers with explicitly unassessed currency bias."""

from datetime import timezone
from decimal import Decimal
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.macro_drivers import RateDriver, RatePoint, describe_rate, rate_receipt_status
from ai_quant_os.macro_sources import (
    MacroWorldEvidence,
    MacroRatesEvidence,
    MacroEventsEvidence,
    MissingMacroEvidence,
)


class CurrencyMacroAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: Literal["EUR", "GBP", "JPY", "USD"]
    bias: Literal["UNASSESSED"] = "UNASSESSED"
    driver_indices: tuple[Annotated[int, Field(ge=0, le=2)], ...] = Field(max_length=3)
    scheduled_event_count: int | None = Field(ge=0, le=128)
    detected_event_count: int | None = Field(ge=0, le=128)
    may_add_risk: Literal[False] = False


class CurrencyMacroBias(AgentEnvelope):
    agent_id: Literal["A03"] = "A03"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["CurrencyMacroBias"] = "CurrencyMacroBias"
    confidence: Decimal = Field(ge=0, le=0, allow_inf_nan=False)
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_authority: Literal["RISK_KERNEL"] = "RISK_KERNEL"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    world_state_version: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    observation_as_of: AwareDatetime
    rate_capture_ref: str | None
    rate_received_at: AwareDatetime | None
    rate_receipt_status: Literal["MISSING", "FUTURE_RECEIPT", "OLD_RECEIPT", "RECENT_RECEIPT"]
    rate_capture_mode: Literal["ECB_HTTPS_RETRIEVAL", "LOCAL_CSV_IMPORT"] | None
    event_feed_status: Literal["FRESH", "MISSING", "STALE", "FUTURE"]
    event_feed_ref: str | None
    macro_release_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    central_bank_text_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    yield_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    historical_vintage_guarantee: Literal[False] = False
    drivers: tuple[RateDriver, ...] = Field(max_length=3)
    currencies: tuple[CurrencyMacroAssessment, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def checked_drivers(self) -> "CurrencyMacroBias":
        if (self.event_feed_status == "MISSING") != (self.event_feed_ref is None):
            raise ValueError("Macro event reference contradicts availability")
        if (
            (self.rate_capture_ref is None) != (self.rate_received_at is None)
            or (self.rate_capture_mode is None) != (self.rate_received_at is None)
            or self.rate_receipt_status
            != rate_receipt_status(self.rate_received_at, self.observation_as_of)
            or len(self.drivers)
            != (3 if self.rate_receipt_status in {"RECENT_RECEIPT", "OLD_RECEIPT"} else 0)
        ):
            raise ValueError("Macro rate receipt claims disagree")
        if (
            tuple(row.currency for row in self.currencies) != ("EUR", "GBP", "JPY", "USD")
            or (self.rate_receipt_status in {"MISSING", "FUTURE_RECEIPT"} and self.drivers)
            or (
                self.drivers
                and tuple(row.rate_id for row in self.drivers) != ("DFR", "MLFR", "MRR_FR")
            )
        ):
            raise ValueError("Macro driver coverage mismatch")
        for driver in self.drivers:
            if driver.cutoff != self.observation_as_of.astimezone(
                timezone.utc
            ).date() or driver != describe_rate(
                driver.rate_id, driver.observations, cutoff=driver.cutoff
            ):
                raise ValueError("Macro driver disagrees with dated rate facts")
        for row in self.currencies:
            if self.event_feed_status == "FRESH" and (
                row.scheduled_event_count is None
                or row.detected_event_count is None
                or row.scheduled_event_count + row.detected_event_count > 128
            ):
                raise ValueError("Macro event count coverage mismatch")
            if row.driver_indices != tuple(
                index
                for index, driver in enumerate(self.drivers)
                if driver.currency == row.currency
            ) or (
                self.event_feed_status != "FRESH"
                and (row.scheduled_event_count is not None or row.detected_event_count is not None)
            ):
                raise ValueError("Macro currency driver association mismatch")
        return self


async def assess_macro(invocation: AgentInvocation) -> CurrencyMacroBias:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A03", "0.2.0", "READY"):
        raise ValueError("Macro mission context mismatch")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    world = MacroWorldEvidence.model_validate(inputs["world_state"]).world
    missing = MissingMacroEvidence.model_validate(inputs["macro_feed"])
    rates = MacroRatesEvidence.model_validate(inputs["rates_yields"])
    events = MacroEventsEvidence.model_validate(inputs["event_feed"])
    if (
        any(
            row.as_of != world.as_of or row.world_state_version != world.world_state_version
            for row in (missing, rates, events)
        )
        or world.as_of > context.created_at
        or context.valid_until > world.valid_until
        or (
            rates.receipt_status == "RECENT_RECEIPT"
            and (context.valid_until - rates.capture.received_at).total_seconds() > 300
        )
        or (
            events.evidence.status == "FRESH"
            and context.valid_until > events.evidence.feed.valid_until
        )
    ):
        raise ValueError("Macro source observation or expiry mismatch")
    drivers = (
        tuple(
            describe_rate(
                row.rate_id,
                tuple(RatePoint(point.period, point.value) for point in row.observations),
                cutoff=world.as_of.astimezone(timezone.utc).date(),
            )
            for row in rates.capture.series
        )
        if rates.receipt_status in {"RECENT_RECEIPT", "OLD_RECEIPT"}
        else ()
    )
    feed = events.evidence.feed if events.evidence.status == "FRESH" else None
    currencies = tuple(
        CurrencyMacroAssessment(
            currency=currency,
            driver_indices=tuple(
                index for index, driver in enumerate(drivers) if driver.currency == currency
            ),
            scheduled_event_count=sum(currency in row.currencies for row in feed.economic_calendar)
            if feed
            else None,
            detected_event_count=sum(
                currency in row.currencies for row in feed.event_classifications
            )
            if feed
            else None,
        )
        for currency in ("EUR", "GBP", "JPY", "USD")
    )
    contradictions = [
        "NO_MACRO_RELEASE_FEED",
        "NO_CENTRAL_BANK_TEXT",
        "NO_YIELD_FEED",
        "NO_NON_EUR_RATE_FEED",
        "BIAS_CALIBRATION_UNAVAILABLE",
        "WORLD_MACRO_FEED_NOT_CONNECTED",
        "RATE_RECEIPT_" + rates.receipt_status,
        "EVENT_FEED_" + events.evidence.status,
    ]
    if rates.capture and rates.capture.capture_mode == "LOCAL_CSV_IMPORT":
        contradictions.append("RATE_CAPTURE_LOCAL_IMPORT")
    if events.evidence.feed:
        contradictions.append("EVENT_FEED_OPERATOR_SYNTHETIC")
    if any(driver.future_periods for driver in drivers):
        contradictions.append("FUTURE_RATE_PERIODS_WITHHELD")
    return CurrencyMacroBias(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(contradictions),
        trace_id=str(uuid4()),
        world_state_version=world.world_state_version,
        observation_as_of=world.as_of,
        rate_capture_ref=rates.capture_ref,
        rate_received_at=rates.capture.received_at if rates.capture else None,
        rate_receipt_status=rates.receipt_status,
        rate_capture_mode=rates.capture.capture_mode if rates.capture else None,
        event_feed_status=events.evidence.status,
        event_feed_ref=events.evidence.feed_ref,
        drivers=drivers,
        currencies=currencies,
    )


def macro_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A03", "0.2.0").manifest_ref, CurrencyMacroBias, assess_macro
    )
