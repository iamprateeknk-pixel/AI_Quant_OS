"""Source-bound A13 portfolio evidence with explicit missing allocation inputs.

The currency-twin source added by :func:`allocator_sources_with_twin` is
optional evidence only: the A13 allocator's context still compiles from
``INPUT_NAMES``, so allocation behaviour is unchanged whether or not the
twin source is present -- the bounded signal is published for consumers,
never auto-traded.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.currency_twin import CurrencyTwin, pair_score
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


class TwinPairObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    availability: Literal["AVAILABLE"] = "AVAILABLE"
    instrument_id: str = Field(min_length=1, max_length=32)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    pair_score: Decimal = Field(ge=-1, le=1, allow_inf_nan=False)
    combined_confidence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    coverage: Literal["SINGLE_INSTRUMENT"] = "SINGLE_INSTRUMENT"


class WeightedTwinPairObservation(TwinPairObservation):
    """Twin observation extended with the risk-policy evidence weighting.

    Used only when a :class:`PaperRiskPolicy` is supplied to
    :func:`allocator_sources_with_twin`; the unweighted ``pair_score``
    stays as-is and ``weighted_pair_score`` carries the policy-scaled
    signal, so the no-policy payload is untouched.
    """

    twin_evidence_weight: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    weighted_pair_score: Decimal = Field(ge=-1, le=1, allow_inf_nan=False)


def apply_twin_weight(pair_score: Decimal, weight: Decimal) -> Decimal:
    """Scale ``pair_score`` by ``weight``, clamped to ``[-weight, +weight]``.

    The weighting never widens the signal: the result satisfies
    ``abs(result) <= min(abs(pair_score), weight)``. ``weight`` must be a
    finite Decimal in [0, 1] and ``pair_score`` a finite Decimal.
    """
    for label, number in (("pair_score", pair_score), ("weight", weight)):
        if not isinstance(number, Decimal) or not number.is_finite():
            raise ValueError(f"Twin weight {label} must be a finite Decimal")
    if weight < 0 or weight > 1:
        raise ValueError("Twin evidence weight must lie in [0, 1]")
    return max(-weight, min(weight, weight * pair_score))


def twin_pair_signal(
    twin_a: CurrencyTwin,
    twin_b: CurrencyTwin,
    *,
    microstructure: Decimal = Decimal(0),
    regime_state: Decimal = Decimal(0),
    event_state: Decimal = Decimal(0),
    costs: Decimal = Decimal(0),
) -> Decimal:
    """Bounded twin signal in [-1, 1] for the pair ``twin_a`` / ``twin_b``.

    Delegates to :func:`ai_quant_os.currency_twin.pair_score` and clamps the
    result into [-1, 1]; the result is exactly ``Decimal(0)`` when the combined
    confidence is below the ``pair_score`` confidence floor.
    """
    score = pair_score(
        twin_a,
        twin_b,
        microstructure=microstructure,
        regime_state=regime_state,
        event_state=event_state,
        costs=costs,
    )
    return max(Decimal(-1), min(Decimal(1), score))


def allocator_sources_with_twin(
    inputs: AllocationInputs,
    *,
    now: datetime,
    twin_a: CurrencyTwin | None = None,
    twin_b: CurrencyTwin | None = None,
    microstructure: Decimal = Decimal(0),
    regime_state: Decimal = Decimal(0),
    event_state: Decimal = Decimal(0),
    costs: Decimal = Decimal(0),
    risk_policy: PaperRiskPolicy | None = None,
) -> dict[str, SourceBinding]:
    """Allocator sources, optionally extended with a currency-twin source.

    The twin source is optional evidence only: the A13 allocator's context
    still compiles from ``INPUT_NAMES``, so allocation behaviour is unchanged
    whether or not the twin source is present -- the bounded signal is
    published for consumers, never auto-traded. With neither twin supplied
    this returns exactly ``allocator_sources(inputs, now=now)``.

    When ``risk_policy`` is supplied, the twin observation additionally
    carries the policy's ``twin_evidence_weight`` and a
    ``weighted_pair_score`` (see :func:`apply_twin_weight`); with
    ``risk_policy=None`` the observation payload is exactly the Phase 2
    :class:`TwinPairObservation` payload.
    """
    if risk_policy is not None and type(risk_policy) is not PaperRiskPolicy:
        raise ValueError("Twin weighting requires a PaperRiskPolicy")
    sources = allocator_sources(inputs, now=now)
    if twin_a is None and twin_b is None:
        return sources
    if twin_a is None or twin_b is None:
        raise ValueError("Currency twin evidence requires both legs of the pair")
    instrument_id = inputs.portfolio.instrument_id
    if len(instrument_id) != 6 or not all("A" <= char <= "Z" for char in instrument_id):
        raise ValueError("Currency twin evidence requires a six-letter instrument id")
    base_currency, quote_currency = instrument_id[:3], instrument_id[3:]
    if twin_a.currency != base_currency or twin_b.currency != quote_currency:
        raise ValueError("Currency twin evidence does not match the portfolio instrument")
    signal = twin_pair_signal(
        twin_a,
        twin_b,
        microstructure=microstructure,
        regime_state=regime_state,
        event_state=event_state,
        costs=costs,
    )
    combined = min(twin_a.overall_confidence(), twin_b.overall_confidence())
    if risk_policy is None:
        observation: TwinPairObservation = TwinPairObservation(
            as_of=now,
            instrument_id=instrument_id,
            base_currency=base_currency,
            quote_currency=quote_currency,
            pair_score=signal,
            combined_confidence=combined,
        )
    else:
        weight = risk_policy.twin_evidence_weight
        observation = WeightedTwinPairObservation(
            as_of=now,
            instrument_id=instrument_id,
            base_currency=base_currency,
            quote_currency=quote_currency,
            pair_score=signal,
            combined_confidence=combined,
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

    # "CURRENCY_TWIN_READ" is not a registered read tool: SourceBinding
    # rejects any tool outside READ_TOOLS, so the twin evidence uses
    # WORLD_STATE_READ -- the same tool currency_strength_sources binds
    # for its currency_twins input.
    sources["currency_twin"] = SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
    return sources
