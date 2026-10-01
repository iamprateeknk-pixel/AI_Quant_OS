"""A02 shadow mission: multi-window paper regimes with explicit evidence gaps."""

import json
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.regime_features import HorizonFeatures, WINDOWS, regime_scores
from ai_quant_os.world_state import WorldState


class HorizonAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    horizon: Literal["SHORT", "MEDIUM", "LONG"]
    status: Literal["AVAILABLE", "INSUFFICIENT", "UNAVAILABLE"]
    bar_count: int
    observed_span_seconds: Decimal | None
    regime: Literal["TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"]
    distribution: dict[str, Decimal]
    distribution_kind: Literal["UNCALIBRATED_FEATURE_SCORES"] = "UNCALIBRATED_FEATURE_SCORES"

    @model_validator(mode="after")
    def scores(self) -> "HorizonAssessment":
        if (
            set(self.distribution) != {"TRENDING", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}
            or any(not x.is_finite() or not 0 <= x <= 1 for x in self.distribution.values())
            or sum(self.distribution.values()) != 1
            or not 0 <= self.bar_count <= 64
        ):
            raise ValueError("Invalid regime score distribution")
        return self


class InstrumentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    horizons: tuple[HorizonAssessment, HorizonAssessment, HorizonAssessment]
    event_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    crisis_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"


class RegimeDistribution(AgentEnvelope):
    agent_id: Literal["A02"] = "A02"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["RegimeDistribution"] = "RegimeDistribution"
    policy_version: Literal["paper-multi-window-v1"] = "paper-multi-window-v1"
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    calibration_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    assessments: tuple[InstrumentAssessment, ...]


def _indexed(payload: dict) -> dict:
    rows = payload["features"]
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError("Invalid regime input feature count")
    result = {row["instrument_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate regime input instruments")
    return result


async def analyze_regimes(invocation: AgentInvocation) -> RegimeDistribution:
    context = invocation.context
    if (context.agent_id, context.agent_version, context.status) != ("A02", "0.2.0", "READY"):
        raise ValueError("Regime mission requires its complete versioned context")
    inputs = {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    world = TypeAdapter(WorldState).validate_python(inputs["world_state"])
    if world.source != "PAPER_SIMULATED" or world.policy_version != "paper-regime-v1":
        raise ValueError("Unsupported regime world source")
    for name in ("price_features", "volatility_features", "session_state"):
        if inputs[name]["world_state_version"] != world.world_state_version:
            raise ValueError("Regime inputs refer to different worlds")
    session = inputs["session_state"]
    if (
        session["clock_basis"] != "UTC"
        or session["as_of"] != world.as_of.isoformat()
        or any(
            session[key] != "UNAVAILABLE"
            for key in ("calendar_status", "event_status", "crisis_status")
        )
    ):
        raise ValueError("Unsupported session evidence contract")
    prices, volatility = _indexed(inputs["price_features"]), _indexed(inputs["volatility_features"])
    if not set(prices).issubset({x.instrument_id for x in world.instruments}) or not set(
        volatility
    ).issubset({x.instrument_id for x in world.instruments}):
        raise ValueError("Unknown regime feature instrument")
    contradictions = [
        "CALIBRATION_UNAVAILABLE",
        "SESSION_CALENDAR_UNAVAILABLE",
        "EVENT_EVIDENCE_UNAVAILABLE",
        "CRISIS_EVIDENCE_UNAVAILABLE",
    ]
    assessments = []
    for item in world.instruments:
        rows = []
        usable = item.quote_status == item.history_status == "FRESH"
        price, vol = prices.get(item.instrument_id), volatility.get(item.instrument_id)
        if usable and price is not None and vol is not None:
            if (
                Decimal(price["mid_price"]) != item.mid_price
                or tuple(price["evidence_refs"]) != item.evidence_refs
                or Decimal(vol["realized_volatility_bps"]) != item.realized_volatility_bps
                or tuple(vol["evidence_refs"]) != item.evidence_refs
            ):
                raise ValueError("Regime feature provenance mismatch")
            features = tuple(
                TypeAdapter(HorizonFeatures).validate_python(row) for row in price["horizons"]
            )
            if features and tuple(x.horizon for x in features) != tuple(x[0] for x in WINDOWS):
                raise ValueError("Regime horizons mismatch")
        else:
            features = ()
        if not features:
            features = tuple(
                HorizonFeatures(name, "UNAVAILABLE", 0, None, None, None, None, None, None)
                for name, _, _ in WINDOWS
            )
            contradictions.append(item.instrument_id + ":MARKET_EVIDENCE_UNAVAILABLE")
        for feature in features:
            if feature.status == "AVAILABLE" and (
                feature.start is None
                or feature.end is None
                or not feature.start < feature.end <= world.as_of
                or feature.volatility_bps is None
                or feature.volatility_bps < 0
                or feature.efficiency is None
                or not 0 <= feature.efficiency <= 1
            ):
                raise ValueError("Invalid regime horizon evidence")
            label, scores = regime_scores(feature)
            rows.append(
                HorizonAssessment(
                    horizon=feature.horizon,
                    status=feature.status,
                    bar_count=feature.bar_count,
                    observed_span_seconds=feature.observed_span_seconds,
                    regime=label,
                    distribution=scores,
                )
            )
        if len({x.regime for x in rows if x.status == "AVAILABLE"}) > 1:
            contradictions.append(item.instrument_id + ":HORIZON_DISAGREEMENT")
        if any(x.status == "INSUFFICIENT" for x in rows):
            contradictions.append(item.instrument_id + ":HISTORY_INSUFFICIENT")
        assessments.append(
            InstrumentAssessment(instrument_id=item.instrument_id, horizons=tuple(rows))
        )
    return RegimeDistribution(
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


def regime_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        manifest_ref=registry.get("A02", "0.2.0").manifest_ref,
        output_schema=RegimeDistribution,
        invoke=analyze_regimes,
    )
