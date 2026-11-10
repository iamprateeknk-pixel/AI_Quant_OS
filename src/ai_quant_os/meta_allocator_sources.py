"""Source-bound A13 portfolio evidence with explicit missing allocation inputs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.currency_twin import CurrencyTwin, pair_score as currency_pair_score
from ai_quant_os.graph_ledger import GraphReceipt
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskLimits
from ai_quant_os.risk_policy import PaperRiskPolicy
from ai_quant_os.strategy_selector_agent import EligibleStrategySetV3

INPUT_NAMES = (
    "strategy_set",
    "portfolio_snapshot",
    "risk_snapshot",
    "covariance",
    "exposure",
)


class StrategySetObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["A12_GRAPH_OUTPUT_NOT_CONNECTED"] = "A12_GRAPH_OUTPUT_NOT_CONNECTED"


class ConnectedStrategySetObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["AVAILABLE"] = "AVAILABLE"
    upstream_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    upstream_context_ref: str = Field(min_length=1, max_length=128)
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["NO_ELIGIBLE_CANDIDATES", "WAIT_FOR_EVIDENCE"]
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    world_state_version: str = Field(min_length=1, max_length=128)
    eligible_candidate_ids: tuple[str, ...] = Field(max_length=100)
    blockers: tuple[str, ...] = Field(max_length=16)


class PortfolioObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    snapshot_id: str = Field(min_length=1, max_length=128)
    coverage: Literal["SINGLE_INSTRUMENT"] = "SINGLE_INSTRUMENT"


class RiskObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    safety_state: Literal[
        "NORMAL", "CAUTION", "REDUCED_RISK", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"
    ]
    max_order_exposure: Decimal = Field(gt=0, allow_inf_nan=False)
    max_portfolio_exposure: Decimal = Field(gt=0, allow_inf_nan=False)
    risk_budget_status: Literal["UNCALIBRATED"] = "UNCALIBRATED"


class CovarianceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["VALIDATED_CANDIDATE_RETURN_MATRIX_UNAVAILABLE"] = (
        "VALIDATED_CANDIDATE_RETURN_MATRIX_UNAVAILABLE"
    )


class ExposureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    snapshot_id: str = Field(min_length=1, max_length=128)
    instrument_id: str = Field(min_length=1, max_length=32)
    filled_net_quantity: Decimal = Field(allow_inf_nan=False)
    pending_buy_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    pending_sell_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    coverage: Literal["SINGLE_INSTRUMENT"] = "SINGLE_INSTRUMENT"


class TwinPairObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["AVAILABLE"] = "AVAILABLE"
    instrument_id: str = Field(pattern=r"^[A-Z]{6}$")
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    pair_score: Decimal = Field(ge=-1, le=1, allow_inf_nan=False)
    combined_confidence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    coverage: Literal["SINGLE_PAIR"] = "SINGLE_PAIR"


class WeightedTwinPairObservation(TwinPairObservation):
    twin_evidence_weight: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    weighted_pair_score: Decimal = Field(ge=-1, le=1, allow_inf_nan=False)


@dataclass(frozen=True)
class AllocationInputs:
    portfolio: PortfolioExposure
    risk_limits: RiskLimits
    safety_state: str


def allocator_sources(inputs: AllocationInputs, *, now: datetime) -> dict[str, SourceBinding]:
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or type(inputs) is not AllocationInputs
        or type(inputs.portfolio) is not PortfolioExposure
        or type(inputs.risk_limits) is not RiskLimits
    ):
        raise ValueError("Current typed paper allocation evidence required")
    payloads = {
        "strategy_set": (
            StrategySetObservation(as_of=now),
            "STRATEGY_REGISTRY_READ",
        ),
        "portfolio_snapshot": (
            PortfolioObservation(as_of=now, snapshot_id=inputs.portfolio.snapshot_id),
            "PORTFOLIO_READ",
        ),
        "risk_snapshot": (
            RiskObservation(
                as_of=now,
                safety_state=inputs.safety_state,
                max_order_exposure=inputs.risk_limits.max_order_exposure,
                max_portfolio_exposure=inputs.risk_limits.max_portfolio_exposure,
            ),
            "PORTFOLIO_READ",
        ),
        "covariance": (CovarianceObservation(as_of=now), "PORTFOLIO_READ"),
        "exposure": (
            ExposureObservation(
                as_of=now,
                snapshot_id=inputs.portfolio.snapshot_id,
                instrument_id=inputs.portfolio.instrument_id,
                filled_net_quantity=inputs.portfolio.filled_net_quantity,
                pending_buy_quantity=inputs.portfolio.pending_buy_quantity,
                pending_sell_quantity=inputs.portfolio.pending_sell_quantity,
            ),
            "PORTFOLIO_READ",
        ),
    }
    sources = {}
    for name, (payload, tool) in payloads.items():
        encoded = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        record = EvidenceRecord(
            source_id=f"meta-allocator:{name}:{sha256(encoded.encode()).hexdigest()}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=encoded,
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(tool, "GENERAL", fetch)
    return sources


def allocator_sources_v3(
    inputs: AllocationInputs,
    *,
    selector_receipt: GraphReceipt,
    selector_output: EligibleStrategySetV3,
    now: datetime,
) -> dict[str, SourceBinding]:
    if (
        type(selector_receipt) is not GraphReceipt
        or type(selector_output) is not EligibleStrategySetV3
        or selector_receipt.status != "COMPLETED"
        or (selector_receipt.agent_id, selector_receipt.agent_version) != ("A12", "0.3.0")
        or selector_receipt.output_json is None
        or selector_receipt.created_at > now
        or selector_receipt.valid_until <= now
    ):
        raise ValueError("Current completed A12 0.3.0 graph evidence required")
    canonical_output = json.dumps(
        selector_output.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical_output != selector_receipt.output_json:
        raise ValueError("A12 output does not match its retained graph receipt")
    strategy_set = ConnectedStrategySetObservation(
        as_of=selector_receipt.created_at,
        upstream_run_id=selector_receipt.run_id,
        upstream_context_ref=selector_receipt.context_ref,
        output_sha256=sha256(selector_receipt.output_json.encode()).hexdigest(),
        state=selector_output.state,
        registry_sha256=selector_output.registry_sha256,
        world_state_version=selector_output.world_state_version,
        eligible_candidate_ids=tuple(
            row.candidate_id for row in selector_output.eligible_strategies
        ),
        blockers=selector_output.blockers,
    )
    sources = allocator_sources(inputs, now=now)
    encoded = json.dumps(
        strategy_set.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    evidence = EvidenceRecord(
        source_id=f"meta-allocator:strategy_set:{sha256(encoded.encode()).hexdigest()}",
        as_of=selector_receipt.created_at,
        valid_until=min(selector_receipt.valid_until, now + timedelta(seconds=5)),
        trusted=True,
        payload_json=encoded,
    )

    async def fetch(record=evidence):
        return record

    sources["strategy_set"] = SourceBinding("STRATEGY_REGISTRY_READ", "GENERAL", fetch)
    return sources


def twin_pair_signal(
    twin_a: CurrencyTwin,
    twin_b: CurrencyTwin,
    *,
    microstructure: Decimal = Decimal("0"),
) -> Decimal:
    """Pair signal for two currency twins, clamped to [-1, 1].

    The blueprint pair formula from :mod:`ai_quant_os.currency_twin`
    (twin difference scaled by the weaker leg's confidence, plus the
    microstructure adjustment) applies, including its confidence floor:
    below the floor the score is exactly zero. The result is clamped to
    [-1, 1] so no microstructure adjustment can push the signal outside
    the range the allocator treats as a full-strength pair view.
    """
    if type(twin_a) is not CurrencyTwin or type(twin_b) is not CurrencyTwin:
        raise ValueError("Twin pair signal requires two currency twins")
    if type(microstructure) is not Decimal or not microstructure.is_finite():
        raise ValueError("Twin pair microstructure must be a finite Decimal")
    raw = currency_pair_score(
        twin_a,
        twin_b,
        microstructure=microstructure,
        regime_state=Decimal("0"),
        event_state=Decimal("0"),
        costs=Decimal("0"),
    )
    return max(Decimal("-1"), min(Decimal("1"), raw))


def apply_twin_weight(pair_score: Decimal, weight: Decimal) -> Decimal:
    """Scale a clamped pair score by the policy weight, never widening it.

    ``weighted = clamp(pair_score * weight, -weight, +weight)``: with
    ``pair_score`` in [-1, 1] and ``weight`` in [0, 1] the product is
    already inside the clamp, so the weight can only shrink the signal
    toward zero, never amplify it. The confidence floor still zeroes
    ``pair_score`` first (see :func:`twin_pair_signal`).
    """
    if (
        type(pair_score) is not Decimal
        or not pair_score.is_finite()
        or not Decimal("-1") <= pair_score <= Decimal("1")
    ):
        raise ValueError("Twin pair score must be a finite Decimal in [-1, 1]")
    if (
        type(weight) is not Decimal
        or not weight.is_finite()
        or not Decimal("0") <= weight <= Decimal("1")
    ):
        raise ValueError("Twin evidence weight must be a finite Decimal in [0, 1]")
    return max(-weight, min(weight, pair_score * weight))


def allocator_sources_with_twin(
    inputs: AllocationInputs,
    *,
    now: datetime,
    twin_a: CurrencyTwin | None = None,
    twin_b: CurrencyTwin | None = None,
    risk_policy: PaperRiskPolicy | None = None,
    microstructure: Decimal = Decimal("0"),
) -> dict[str, SourceBinding]:
    """Like :func:`allocator_sources`, plus an optional currency-twin source.

    With both twin legs absent the result is exactly
    :func:`allocator_sources` (zero contribution, existing behaviour
    untouched). With both legs present a ``currency_twin`` evidence
    record is added: a :class:`TwinPairObservation` when no risk policy
    is supplied, or a :class:`WeightedTwinPairObservation` (pair score
    scaled by the policy's ``twin_evidence_weight``) when one is. The
    pair must match the portfolio instrument (base + quote currencies),
    exactly one leg raises, and a non-policy ``risk_policy`` fails
    closed. The twin source is published, not consumed: no allocator
    weighting reads it yet.
    """
    if risk_policy is not None and type(risk_policy) is not PaperRiskPolicy:
        raise ValueError("Twin weighting requires a typed paper risk policy")
    if (twin_a is None) != (twin_b is None):
        raise ValueError("Currency twin requires both legs or neither")
    sources = allocator_sources(inputs, now=now)
    if twin_a is None:
        return sources
    if type(twin_a) is not CurrencyTwin or type(twin_b) is not CurrencyTwin:
        raise ValueError("Currency twin legs must be typed currency twins")
    instrument_id = twin_a.currency + twin_b.currency
    if instrument_id != inputs.portfolio.instrument_id:
        raise ValueError("Currency twin pair does not match the portfolio instrument")
    signal = twin_pair_signal(twin_a, twin_b, microstructure=microstructure)
    combined_confidence = min(twin_a.overall_confidence(), twin_b.overall_confidence())
    if risk_policy is None:
        observation: TwinPairObservation = TwinPairObservation(
            as_of=now,
            instrument_id=instrument_id,
            base_currency=twin_a.currency,
            quote_currency=twin_b.currency,
            pair_score=signal,
            combined_confidence=combined_confidence,
        )
    else:
        weight = risk_policy.twin_evidence_weight
        observation = WeightedTwinPairObservation(
            as_of=now,
            instrument_id=instrument_id,
            base_currency=twin_a.currency,
            quote_currency=twin_b.currency,
            pair_score=signal,
            combined_confidence=combined_confidence,
            twin_evidence_weight=weight,
            weighted_pair_score=apply_twin_weight(signal, weight),
        )
    encoded = json.dumps(
        observation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    record = EvidenceRecord(
        source_id=f"meta-allocator:currency_twin:{sha256(encoded.encode()).hexdigest()}",
        as_of=now,
        valid_until=now + timedelta(seconds=5),
        trusted=True,
        payload_json=encoded,
    )

    async def fetch(record=record):
        return record

    # WORLD_STATE_READ is the existing binding currency_strength_sources
    # already uses for its currency-twins input; CURRENCY_TWIN_READ is
    # not a registered tool, so no new tool is invented here.
    sources["currency_twin"] = SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
    return sources
