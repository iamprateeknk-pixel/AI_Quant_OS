"""A05 descriptive measurements; hard gates remain solely in the risk kernel."""

from datetime import datetime
from decimal import Context, Decimal, localcontext
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.liquidity_sources import (
    QuotesEvidence,
    BarsEvidence,
    SpreadsEvidence,
    SessionEvidence,
    BrokerTelemetryEvidence,
    verified_liquidity_inputs,
)
from ai_quant_os.volatility_features import VolatilityMetrics, volatility_features


def _wire(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


class LiquidityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    quote_status: Literal["FRESH", "STALE", "FUTURE", "UNTRUSTED", "MISSING"]
    history_status: Literal["FRESH", "STALE", "INSUFFICIENT"]
    quote_usable: bool
    history_usable: bool
    spread_bps: Decimal | None = Field(ge=0, allow_inf_nan=False)
    volatility: VolatilityMetrics
    liquidity_status: Literal["UNAVAILABLE", "SYNTHETIC_L1_OBSERVED"] = "UNAVAILABLE"
    bid_displayed_base_units: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    ask_displayed_base_units: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    displayed_imbalance: Decimal | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    tradeability_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    may_add_risk: Literal[False] = False


class LiquidityState(AgentEnvelope):
    confidence: Decimal = Field(ge=0, le=0, allow_inf_nan=False)
    agent_id: Literal["A05"] = "A05"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["LiquidityState"] = "LiquidityState"
    policy_version: Literal["paper-log-volatility-v1"] = "paper-log-volatility-v1"
    units: Literal["LOG_RETURN_BPS_PER_OBSERVATION"] = "LOG_RETURN_BPS_PER_OBSERVATION"
    abnormality_kind: Literal["UNCALIBRATED_LAST_RETURN_DEVIATION"] = (
        "UNCALIBRATED_LAST_RETURN_DEVIATION"
    )
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    hard_gate_authority: Literal["RISK_KERNEL"] = "RISK_KERNEL"
    session_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    broker_telemetry_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    assessments: tuple[LiquidityAssessment, ...] = Field(min_length=1, max_length=16)

    @field_serializer("assessments", when_used="json")
    def assessments_json(self, rows: tuple[LiquidityAssessment, ...]) -> list:
        return [_wire(row.model_dump(mode="python")) for row in rows]


async def assess_liquidity(invocation: AgentInvocation) -> LiquidityState:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A05", "0.2.0", "READY"):
        raise ValueError("Liquidity mission context mismatch")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    quotes = QuotesEvidence.model_validate(inputs["quotes"])
    bars = BarsEvidence.model_validate(inputs["bars"])
    spreads = SpreadsEvidence.model_validate(inputs["spreads"])
    session = SessionEvidence.model_validate(inputs["session_state"])
    telemetry = BrokerTelemetryEvidence.model_validate(inputs["broker_telemetry"])
    world = quotes.world
    if (
        session.world_state_version != world.world_state_version
        or telemetry.world_state_version != world.world_state_version
        or world.as_of > context.created_at
        or world.valid_until < context.valid_until
    ):
        raise ValueError("Liquidity inputs refer to different worlds")
    histories = verified_liquidity_inputs(quotes, bars, spreads)
    contradictions = [
        "SESSION_EVIDENCE_UNAVAILABLE",
        "BROKER_TELEMETRY_UNAVAILABLE",
        "EXECUTABLE_LIQUIDITY_UNVERIFIED",
        "CALIBRATION_UNAVAILABLE",
    ]
    assessments = []
    depths = {row.instrument_id: row for row in quotes.displayed_depth}
    for item in world.instruments:
        metrics = volatility_features(histories.get(item.instrument_id, ()))
        depth = depths.get(item.instrument_id) if item.quote_status == "FRESH" else None
        imbalance = None
        if depth is not None:
            with localcontext(Context(prec=50)):
                total = depth.bid_size + depth.ask_size
                if total:
                    imbalance = (depth.bid_size - depth.ask_size) / total
        else:
            contradictions.append(item.instrument_id + ":DISPLAYED_DEPTH_UNAVAILABLE")
        if item.quote_status != "FRESH":
            contradictions.append(item.instrument_id + ":QUOTE_" + item.quote_status)
        if item.history_status != "FRESH":
            contradictions.append(item.instrument_id + ":HISTORY_" + item.history_status)
        if metrics.abnormality_status in {"DEGENERATE_BASELINE", "IRREGULAR_CADENCE"}:
            contradictions.append(item.instrument_id + ":" + metrics.abnormality_status)
        assessments.append(
            LiquidityAssessment(
                instrument_id=item.instrument_id,
                quote_status=item.quote_status,
                history_status=item.history_status,
                quote_usable=item.quote_status == "FRESH",
                history_usable=item.history_status == "FRESH",
                spread_bps=item.spread_bps if item.quote_status == "FRESH" else None,
                volatility=metrics,
                liquidity_status="SYNTHETIC_L1_OBSERVED" if depth else "UNAVAILABLE",
                bid_displayed_base_units=depth.bid_size if depth else None,
                ask_displayed_base_units=depth.ask_size if depth else None,
                displayed_imbalance=imbalance,
            )
        )
    return LiquidityState(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(contradictions),
        trace_id=str(uuid4()),
        assessments=tuple(assessments),
    )


def liquidity_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A05", "0.2.0").manifest_ref, LiquidityState, assess_liquidity
    )
