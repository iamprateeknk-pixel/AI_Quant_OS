"""Source-bound A08 research recipes and archival-only cost evidence."""

from datetime import timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.research_data_contracts import ResearchDataReceipt
from ai_quant_os.research_hypothesis_policy import ResearchRule, research_rule


class ResearchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: AwareDatetime
    collection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DirectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    receipt: ResearchDataReceipt
    eligible_pairs: int = Field(strict=True, ge=0, le=4094)
    same_direction_pairs: int = Field(strict=True, ge=0, le=4094)

    @model_validator(mode="after")
    def training_counts(self):
        if self.receipt.resource_class != "TRAINING" or not (
            self.same_direction_pairs <= self.eligible_pairs <= self.receipt.row_count - 2
        ):
            raise ValueError("Invalid training direction evidence")
        return self


class HistoricalResearchEvidence(ResearchObservation):
    datasets: tuple[DirectionEvidence, ...] = Field(max_length=64)
    coverage: Literal["ALL_CHECKED_TRAINING_DATASETS"] = "ALL_CHECKED_TRAINING_DATASETS"

    @model_validator(mode="after")
    def unique_observations(self):
        if len({row.receipt.dataset_ref for row in self.datasets}) != len(self.datasets) or any(
            row.receipt.received_at > self.as_of for row in self.datasets
        ):
            raise ValueError("Duplicate or future research catalog evidence")
        return self


class ResearchMemoryEvidence(ResearchObservation):
    completed_run_count: int = Field(strict=True, ge=0, le=128)
    known_signatures: tuple[str, ...] = Field(max_length=512)
    coverage: Literal["CHECKED_A08_0_2_COMPLETED_GRAPH_OUTPUTS"] = (
        "CHECKED_A08_0_2_COMPLETED_GRAPH_OUTPUTS"
    )

    @model_validator(mode="after")
    def bounded_signatures(self):
        if (
            self.known_signatures != tuple(sorted(set(self.known_signatures)))
            or len(self.known_signatures) > self.completed_run_count * 4
            or any(
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                for value in self.known_signatures
            )
        ):
            raise ValueError("Invalid research memory signatures")
        return self


class ArchivedSpread(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    quote_status: Literal["MISSING", "FRESH", "STALE", "FUTURE", "UNTRUSTED"]
    spread_bps: Decimal | None = Field(ge=0, le=20000, allow_inf_nan=False)


class ResearchWorldSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    world_ref: str = Field(pattern=r"^world:[a-f0-9]{64}$")
    as_of: AwareDatetime
    valid_until: AwareDatetime
    spreads: tuple[ArchivedSpread, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def archive_coverage(self):
        if self.valid_until <= self.as_of or tuple(
            row.instrument_id for row in self.spreads
        ) != tuple(row.instrument_id for row in PAPER_INSTRUMENTS):
            raise ValueError("Invalid research world archive coverage")
        return self


class ResearchWorldEvidence(ResearchObservation):
    snapshots: tuple[ResearchWorldSummary, ...] = Field(max_length=64)
    basis: Literal["HASH_CHECKED_LOCAL_PAPER_WORLD_ARCHIVES"] = (
        "HASH_CHECKED_LOCAL_PAPER_WORLD_ARCHIVES"
    )
    freshness_claim: Literal["ARCHIVAL_ONLY"] = "ARCHIVAL_ONLY"

    @model_validator(mode="after")
    def nonfuture_unique_archives(self):
        if len({row.world_ref for row in self.snapshots}) != len(self.snapshots) or any(
            row.as_of > self.as_of for row in self.snapshots
        ):
            raise ValueError("Duplicate or future research world archives")
        return self


class HypothesisInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    historical_catalog: HistoricalResearchEvidence
    research_memory: ResearchMemoryEvidence
    world_state_archives: ResearchWorldEvidence

    @model_validator(mode="after")
    def observation_boundary(self):
        if not (
            self.historical_catalog.as_of
            == self.research_memory.as_of
            == self.world_state_archives.as_of
        ):
            raise ValueError("Research source capture boundaries differ")
        return self


class ResearchExpectedCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["UNAVAILABLE", "HISTORICAL_SPREAD_ONLY"]
    spread_observation_count: int = Field(strict=True, ge=0, le=64)
    minimum_recorded_spread_bps: Decimal | None = Field(ge=0, le=20000, allow_inf_nan=False)
    maximum_recorded_spread_bps: Decimal | None = Field(ge=0, le=20000, allow_inf_nan=False)
    commission_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    slippage_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    financing_status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    source: Literal["ARCHIVED_PAPER_QUOTES_ONLY"] = "ARCHIVED_PAPER_QUOTES_ONLY"

    @model_validator(mode="after")
    def cost_availability(self):
        if self.spread_observation_count == 0:
            if (
                self.status != "UNAVAILABLE"
                or self.minimum_recorded_spread_bps is not None
                or self.maximum_recorded_spread_bps is not None
            ):
                raise ValueError("Missing cost evidence cannot claim estimates")
        elif (
            self.status != "HISTORICAL_SPREAD_ONLY"
            or self.minimum_recorded_spread_bps is None
            or self.maximum_recorded_spread_bps is None
            or self.minimum_recorded_spread_bps > self.maximum_recorded_spread_bps
        ):
            raise ValueError("Invalid archival spread range")
        return self


REQUIRED_RESEARCH_DATA = (
    "SEPARATE_CHRONOLOGICAL_OUT_OF_SAMPLE_BARS",
    "MEASURED_BROKER_COST_MODEL",
    "CAUSAL_NEXT_BAR_OPEN_REPLAY",
    "PREDECLARED_MULTIPLE_TESTING_POLICY",
)


class ResearchHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$")
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    development_end: AwareDatetime
    rule: ResearchRule
    description: str = Field(min_length=1, max_length=1024)
    eligible_pairs: int = Field(strict=True, ge=8, le=4094)
    same_direction_pairs: int = Field(strict=True, ge=0, le=4094)
    observed_direction_persistence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    execution_timing: Literal["NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"] = (
        "NEXT_BAR_OPEN_AFTER_PREVIOUS_CLOSE_SIGNAL"
    )
    signal_history_bars: Literal[2] = 2
    required_data: tuple[str, ...] = REQUIRED_RESEARCH_DATA
    falsification_test: Literal[
        "REJECT_IF_NET_OUT_OF_SAMPLE_PNL_NONPOSITIVE_OR_COST_STRESS_FAILS_OR_PATTERN_NOT_REPLICATED"
    ] = "REJECT_IF_NET_OUT_OF_SAMPLE_PNL_NONPOSITIVE_OR_COST_STRESS_FAILS_OR_PATTERN_NOT_REPLICATED"
    expected_costs: ResearchExpectedCosts
    validation_status: Literal["NOT_RUN"] = "NOT_RUN"
    alpha_acceptance: Literal["UNASSESSED"] = "UNASSESSED"

    @property
    def signature(self) -> str:
        return sha256(
            json.dumps(
                [self.instrument_id, self.dataset_fingerprint, self.rule], separators=(",", ":")
            ).encode()
        ).hexdigest()


def read_hypothesis_inputs(context: CompiledContext) -> HypothesisInputs:
    verify_context(context)
    if (context.agent_id, context.agent_version, context.scope, context.status) != (
        "A08",
        "0.2.0",
        "SANDBOX",
        "READY",
    ):
        raise ValueError("Research requires its complete sandbox context")
    expected = (
        ("historical_catalog", "HISTORY_READ", "TRAINING"),
        ("research_memory", "RESEARCH_MEMORY_READ", "TRAINING"),
        ("world_state_archives", "WORLD_STATE_READ", "GENERAL"),
    )
    if (
        tuple((row.input_name, row.source_tool, row.resource_class) for row in context.inputs)
        != expected
    ):
        raise ValueError("Research context resource classification mismatch")
    inputs = HypothesisInputs.model_validate(
        {row.input_name: json.loads(row.payload_json) for row in context.inputs}
    )
    if (
        inputs.historical_catalog.as_of > context.created_at
        or context.valid_until > inputs.historical_catalog.as_of + timedelta(seconds=5)
    ):
        raise ValueError("Research context exceeds source capture validity")
    return inputs


def hypothesis_recipe(context: CompiledContext) -> dict:
    inputs = read_hypothesis_inputs(context)
    proposed = {}
    for row in inputs.historical_catalog.datasets:
        rule = research_rule(row.eligible_pairs, row.same_direction_pairs)
        if rule is None:
            continue
        receipt = row.receipt
        spreads = [
            item.spread_bps
            for snapshot in inputs.world_state_archives.snapshots
            for item in snapshot.spreads
            if item.instrument_id == receipt.instrument_id
            and item.quote_status == "FRESH"
            and item.spread_bps is not None
        ]
        costs = ResearchExpectedCosts(
            status="HISTORICAL_SPREAD_ONLY" if spreads else "UNAVAILABLE",
            spread_observation_count=len(spreads),
            minimum_recorded_spread_bps=min(spreads) if spreads else None,
            maximum_recorded_spread_bps=max(spreads) if spreads else None,
        )
        with localcontext(Context(prec=28)):
            persistence = Decimal(row.same_direction_pairs) / row.eligible_pairs
        hypothesis = ResearchHypothesis(
            dataset_ref=receipt.dataset_ref,
            dataset_fingerprint=receipt.dataset_fingerprint,
            instrument_id=receipt.instrument_id,
            development_end=receipt.last_observed_at,
            rule=rule,
            description=f"For {receipt.instrument_id}, test whether {rule.lower().replace('_', ' ')} at the next bar open yields positive net P&L on separate later data after measured costs.",
            eligible_pairs=row.eligible_pairs,
            same_direction_pairs=row.same_direction_pairs,
            observed_direction_persistence=persistence,
            expected_costs=costs,
        )
        proposed.setdefault(hypothesis.signature, hypothesis)
    known = set(inputs.research_memory.known_signatures)
    unseen = [row for signature, row in proposed.items() if signature not in known]
    unseen.sort(
        key=lambda row: (
            -Fraction(abs(2 * row.same_direction_pairs - row.eligible_pairs), row.eligible_pairs),
            row.signature,
        )
    )
    hypotheses = tuple(unseen[:4])
    contradictions = {
        "CALIBRATION_UNAVAILABLE",
        "MULTIPLE_TESTING_UNASSESSED",
        "MARKET_AUTHENTICITY_UNVERIFIED",
        "EXECUTION_COSTS_INCOMPLETE",
    }
    if not inputs.world_state_archives.snapshots:
        contradictions.add("WORLD_ARCHIVES_EMPTY")
    if not inputs.historical_catalog.datasets:
        contradictions.add("TRAINING_CATALOG_EMPTY")
    return {
        "status": "PROPOSED"
        if hypotheses
        else "NO_NEW_HYPOTHESIS"
        if proposed
        else "WAIT_FOR_EVIDENCE",
        "hypotheses": hypotheses,
        "training_dataset_count": len(inputs.historical_catalog.datasets),
        "eligible_anomaly_count": len(proposed),
        "already_proposed_count": len(set(proposed) & known),
        "deferred_hypothesis_count": max(0, len(unseen) - 4),
        "contradictions": tuple(sorted(contradictions)),
    }


class HypothesisSpec(AgentEnvelope):
    agent_id: Literal["A08"] = "A08"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["HypothesisSpec"] = "HypothesisSpec"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    policy_version: Literal["development-direction-screen-v1"] = "development-direction-screen-v1"
    hard_gate_owner: Literal["ResearchPipeline"] = "ResearchPipeline"
    status: Literal["PROPOSED", "NO_NEW_HYPOTHESIS", "WAIT_FOR_EVIDENCE"]
    hypotheses: tuple[ResearchHypothesis, ...] = Field(max_length=4)
    training_dataset_count: int = Field(strict=True, ge=0, le=64)
    eligible_anomaly_count: int = Field(strict=True, ge=0, le=64)
    already_proposed_count: int = Field(strict=True, ge=0, le=64)
    deferred_hypothesis_count: int = Field(strict=True, ge=0, le=64)
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False
    isolation_verified: Literal[False] = False
    novelty_scope: Literal["PRIOR_COMPLETED_GRAPHS_AT_CAPTURE"] = (
        "PRIOR_COMPLETED_GRAPHS_AT_CAPTURE"
    )
    source_consistency: Literal["PER_STORE_CHECKED_NOT_ATOMIC_ACROSS_STORES"] = (
        "PER_STORE_CHECKED_NOT_ATOMIC_ACROSS_STORES"
    )

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        expected = hypothesis_recipe(context)
        if (
            self.evidence_refs != context.input_refs
            or self.created_at != context.created_at
            or self.valid_until != context.valid_until
            or any(getattr(self, field) != value for field, value in expected.items())
        ):
            raise ValueError("Research hypothesis differs from retained source recipe")
