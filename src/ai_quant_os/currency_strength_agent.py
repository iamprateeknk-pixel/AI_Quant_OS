"""A04 advisory price decomposition with explicit unavailable macro/calibration evidence."""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_serializer

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.currency_strength import CurrencyStrength, currency_strength
from ai_quant_os.currency_strength_sources import (
    CurrencyTwinsEvidence,
    CrossPairReturnsEvidence,
    MacroStrengthEvidence,
    RegimeStrengthEvidence,
    verified_pair_histories,
)
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation


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


class CurrencyScoreVector(AgentEnvelope):
    confidence: Decimal = Field(ge=0, le=0, allow_inf_nan=False)
    agent_id: Literal["A04"] = "A04"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["CurrencyScoreVector"] = "CurrencyScoreVector"
    policy_version: Literal["paper-currency-log-basis-v1"] = "paper-currency-log-basis-v1"
    units: Literal["LOG_RETURN_BPS"] = "LOG_RETURN_BPS"
    rank_scope: Literal["WITHIN_CONNECTED_COMPONENT"] = "WITHIN_CONNECTED_COMPONENT"
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    macro_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    statistical_dependence_status: Literal["NOT_ESTIMATED"] = "NOT_ESTIMATED"
    result: CurrencyStrength

    @field_serializer("result", when_used="json")
    def result_json(self, result: CurrencyStrength) -> dict[str, object]:
        # Decimal's scientific notation does not match its generated JSON Schema.
        return {key: _wire(value) for key, value in asdict(result).items()}


async def assess_currency_strength(invocation: AgentInvocation) -> CurrencyScoreVector:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A04", "0.2.0", "READY"):
        raise ValueError("Currency strength mission context mismatch")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    world = CurrencyTwinsEvidence.model_validate(inputs["currency_twins"]).world
    returns = CrossPairReturnsEvidence.model_validate(inputs["cross_pair_returns"])
    macro = MacroStrengthEvidence.model_validate(inputs["macro_state"])
    regimes = RegimeStrengthEvidence.model_validate(inputs["regime_state"])
    if (
        macro.world_state_version != world.world_state_version
        or regimes.world_state_version != world.world_state_version
        or tuple((row.instrument_id, row.regime) for row in regimes.observations)
        != tuple((item.instrument_id, item.regime) for item in world.instruments)
        or world.as_of > context.created_at
        or world.valid_until < context.valid_until
    ):
        raise ValueError("Currency strength inputs refer to different worlds")
    pairs = verified_pair_histories(world, returns)
    result = currency_strength(tuple(twin.currency for twin in world.currency_twins), pairs)
    contradictions = [
        "MACRO_EVIDENCE_UNAVAILABLE",
        "CALIBRATION_UNAVAILABLE",
        "STATISTICAL_DEPENDENCE_NOT_ESTIMATED",
    ]
    contradictions.extend(
        identifier + ":MARKET_EVIDENCE_UNAVAILABLE" for identifier in returns.excluded_instruments
    )
    if result.status == "INCONSISTENT":
        contradictions.append("INCONSISTENT_CROSS_PAIR_RETURNS")
    elif result.status == "UNALIGNED":
        contradictions.append("UNALIGNED_RETURN_WINDOWS")
    elif result.status == "PARTIAL":
        contradictions.append("DISCONNECTED_CURRENCY_GRAPH")
    elif result.status == "UNAVAILABLE":
        contradictions.append("CURRENCY_RETURNS_UNAVAILABLE")
    if any(row.regime == "UNKNOWN" for row in regimes.observations):
        contradictions.append("REGIME_EVIDENCE_INCOMPLETE")
    return CurrencyScoreVector(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        confidence=Decimal(0),
        uncertainty=Decimal(1),
        created_at=context.created_at,
        valid_until=context.valid_until,
        contradictions=tuple(contradictions),
        trace_id=str(uuid4()),
        result=result,
    )


def currency_strength_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A04", "0.2.0").manifest_ref, CurrencyScoreVector, assess_currency_strength
    )
