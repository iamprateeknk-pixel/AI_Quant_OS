"""Source-bound A13 portfolio evidence with explicit missing allocation inputs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
from ai_quant_os.graph_ledger import GraphReceipt
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskLimits
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
    safety_state: Literal["NORMAL", "CAUTION", "REDUCED_RISK", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"]
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
