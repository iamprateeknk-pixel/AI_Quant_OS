"""A12 reports eligibility blockers; it never creates an order or promotion."""

from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_context import CompiledContext, verify_context
from ai_quant_os.agent_envelope import AgentEnvelope
from ai_quant_os.agent_registry import AgentRegistry
from ai_quant_os.graph_runtime import AgentImplementation, AgentInvocation
from ai_quant_os.strategy_selector_sources import (
    CandidateRegistryObservation,
    INPUT_NAMES,
    ShadowPerformanceObservation,
    UnavailableMetricObservation,
    WorldObservation,
)


class CandidateExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    registry_state: str = Field(min_length=1, max_length=32)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=16)


class EligibleStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    rank: int = Field(strict=True, ge=1, le=100)
    rationale: str = Field(min_length=1, max_length=512)


class EligibleStrategySet(AgentEnvelope):
    agent_id: Literal["A12"] = "A12"
    agent_version: Literal["0.2.0"] = "0.2.0"
    output_type: Literal["EligibleStrategySet"] = "EligibleStrategySet"
    confidence: Decimal = Field(ge=0, le=0, default=Decimal(0))
    uncertainty: Decimal = Field(ge=1, le=1, default=Decimal(1))
    model_route: Literal["DETERMINISTIC_ONLY"] = "DETERMINISTIC_ONLY"
    state: Literal["NO_ELIGIBLE_CANDIDATES", "WAIT_FOR_EVIDENCE"]
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    world_state_version: str = Field(min_length=1, max_length=128)
    eligible_strategies: tuple[EligibleStrategy, ...] = Field(max_length=100)
    excluded_candidates: tuple[CandidateExclusion, ...] = Field(max_length=1000)
    blockers: tuple[str, ...] = Field(max_length=16)
    performance_available: Literal[False] = False
    drift_available: Literal[False] = False
    promotion_allowed: Literal[False] = False

    def require_context(self, context: CompiledContext) -> None:
        super().require_context(context)
        registry, world = _observations(context)
        expected_state, blockers, exclusions = _selection(registry, world, context)
        if (
            self.state,
            self.registry_sha256,
            self.world_state_version,
            self.eligible_strategies,
            self.excluded_candidates,
            self.blockers,
        ) != (
            expected_state,
            registry.registry_sha256,
            world.world_state_version,
            (),
            exclusions,
            blockers,
        ):
            raise ValueError("Selector output differs from current eligibility evidence")


class EligibleStrategySetV3(EligibleStrategySet):
    agent_version: Literal["0.3.0"] = "0.3.0"
    output_type: Literal["EligibleStrategySetV3"] = "EligibleStrategySetV3"

    def require_context(self, context: CompiledContext) -> None:
        AgentEnvelope.require_context(self, context)
        registry, world, performance, _ = _observations_v3(context)
        expected_state, blockers, exclusions = _selection_v3(registry, world, performance, context)
        if (
            self.state,
            self.registry_sha256,
            self.world_state_version,
            self.eligible_strategies,
            self.excluded_candidates,
            self.blockers,
        ) != (
            expected_state,
            registry.registry_sha256,
            world.world_state_version,
            (),
            exclusions,
            blockers,
        ):
            raise ValueError("Selector output differs from current eligibility evidence")


def _observations(context: CompiledContext):
    verify_context(context)
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A12", "0.2.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs)
        != (
            "STRATEGY_REGISTRY_READ",
            "WORLD_STATE_READ",
            "ATTRIBUTION_READ",
            "ATTRIBUTION_READ",
        )
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 4
    ):
        raise ValueError("Selector requires its exact protected paper context")
    registry = CandidateRegistryObservation.model_validate_json(context.inputs[0].payload_json)
    world = WorldObservation.model_validate_json(context.inputs[1].payload_json)
    performance = UnavailableMetricObservation.model_validate_json(context.inputs[2].payload_json)
    drift = UnavailableMetricObservation.model_validate_json(context.inputs[3].payload_json)
    names = ("approved_strategy_registry", "world_state", "performance", "drift")
    if (
        tuple(row.source_id for row in context.inputs)
        != tuple(
            "strategy-selector:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(names, context.inputs, strict=True)
        )
        or any(row.as_of > context.created_at for row in context.inputs)
        or registry.as_of != context.inputs[0].as_of
        or world.as_of != context.inputs[1].as_of
        or performance.as_of != context.inputs[2].as_of
        or drift.as_of != context.inputs[3].as_of
        or performance.input_name != "performance"
        or drift.input_name != "drift"
        or performance.availability != "UNCONNECTED"
        or drift.availability != "UNASSESSED"
    ):
        raise ValueError("Selector observations are stale or inconsistent")
    return registry, world


def _observations_v3(context: CompiledContext):
    verify_context(context)
    if (
        (context.agent_id, context.agent_version, context.scope, context.status)
        != ("A12", "0.3.0", "PAPER", "READY")
        or tuple(row.input_name for row in context.inputs) != INPUT_NAMES
        or tuple(row.source_tool for row in context.inputs)
        != (
            "STRATEGY_REGISTRY_READ",
            "WORLD_STATE_READ",
            "ATTRIBUTION_READ",
            "ATTRIBUTION_READ",
        )
        or tuple(row.resource_class for row in context.inputs) != ("GENERAL",) * 4
    ):
        raise ValueError("Selector requires its exact protected paper context")
    registry = CandidateRegistryObservation.model_validate_json(context.inputs[0].payload_json)
    world = WorldObservation.model_validate_json(context.inputs[1].payload_json)
    performance = ShadowPerformanceObservation.model_validate_json(context.inputs[2].payload_json)
    drift = UnavailableMetricObservation.model_validate_json(context.inputs[3].payload_json)
    names = ("approved_strategy_registry", "world_state", "performance", "drift")
    if (
        tuple(row.source_id for row in context.inputs)
        != tuple(
            "strategy-selector:" + name + ":" + sha256(row.payload_json.encode()).hexdigest()
            for name, row in zip(names, context.inputs, strict=True)
        )
        or any(row.as_of > context.created_at for row in context.inputs)
        or registry.as_of != context.inputs[0].as_of
        or world.as_of != context.inputs[1].as_of
        or performance.as_of != context.inputs[2].as_of
        or drift.as_of != context.inputs[3].as_of
        or performance.fingerprint != sha256(context.inputs[2].payload_json.encode()).hexdigest()
        or drift.input_name != "drift"
        or drift.availability != "UNASSESSED"
    ):
        raise ValueError("Selector observations are stale or inconsistent")
    return registry, world, performance, drift


def _selection(registry, world, context):
    blockers = []
    if not registry.available:
        blockers.append("APPROVED_STRATEGY_REGISTRY_UNAVAILABLE")
    approved = tuple(
        row
        for row in registry.candidates
        if row.state == "APPROVED"
        and row.research_outcome == "VALIDATED"
        and not row.blocking_gates
    )
    if not approved:
        blockers.append("NO_APPROVED_CANDIDATES")
    if world.status != "READY" or world.valid_until <= context.created_at:
        blockers.append("WORLD_STATE_NOT_READY")
    blockers.extend(("CANDIDATE_PERFORMANCE_UNCONNECTED", "CANDIDATE_DRIFT_UNASSESSED"))
    exclusions = []
    for row in registry.candidates:
        reasons = []
        if row.state != "APPROVED":
            reasons.append("REGISTRY_STATE_NOT_APPROVED")
        if row.research_outcome != "VALIDATED":
            reasons.append("VALIDATION_NOT_COMPLETE")
        reasons.extend("BLOCKING_GATE_" + name for name in row.blocking_gates)
        reasons.extend(
            blocker
            for blocker in blockers
            if blocker
            in {
                "WORLD_STATE_NOT_READY",
                "CANDIDATE_PERFORMANCE_UNCONNECTED",
                "CANDIDATE_DRIFT_UNASSESSED",
            }
        )
        if reasons:
            exclusions.append(
                CandidateExclusion(
                    candidate_id=row.candidate_id,
                    registry_state=row.state,
                    reasons=tuple(sorted(set(reasons))),
                )
            )
    return (
        "WAIT_FOR_EVIDENCE" if blockers else "NO_ELIGIBLE_CANDIDATES",
        tuple(sorted(set(blockers))),
        tuple(sorted(exclusions, key=lambda row: row.candidate_id)),
    )


def _selection_v3(registry, world, performance, context):
    blockers = []
    if not registry.available:
        blockers.append("APPROVED_STRATEGY_REGISTRY_UNAVAILABLE")
    approved = tuple(
        row
        for row in registry.candidates
        if row.state == "APPROVED"
        and row.research_outcome == "VALIDATED"
        and not row.blocking_gates
    )
    if not approved:
        blockers.append("NO_APPROVED_CANDIDATES")
    if world.status != "READY" or world.valid_until <= context.created_at:
        blockers.append("WORLD_STATE_NOT_READY")
    if performance.availability == "AVAILABLE":
        blockers.append("CANDIDATE_PERFORMANCE_NOT_QUALIFIED")
    elif performance.availability == "NO_OBSERVATIONS":
        blockers.append("CANDIDATE_SHADOW_PERFORMANCE_MISSING")
    else:
        blockers.append("CANDIDATE_PERFORMANCE_UNCONNECTED")
    blockers.append("CANDIDATE_DRIFT_UNASSESSED")
    exclusions = []
    for row in registry.candidates:
        reasons = []
        if row.state != "APPROVED":
            reasons.append("REGISTRY_STATE_NOT_APPROVED")
        if row.research_outcome != "VALIDATED":
            reasons.append("VALIDATION_NOT_COMPLETE")
        reasons.extend("BLOCKING_GATE_" + name for name in row.blocking_gates)
        reasons.extend(
            blocker
            for blocker in blockers
            if blocker
            in {
                "WORLD_STATE_NOT_READY",
                "CANDIDATE_PERFORMANCE_NOT_QUALIFIED",
                "CANDIDATE_SHADOW_PERFORMANCE_MISSING",
                "CANDIDATE_PERFORMANCE_UNCONNECTED",
                "CANDIDATE_DRIFT_UNASSESSED",
            }
        )
        if reasons:
            exclusions.append(
                CandidateExclusion(
                    candidate_id=row.candidate_id,
                    registry_state=row.state,
                    reasons=tuple(sorted(set(reasons))),
                )
            )
    return (
        "WAIT_FOR_EVIDENCE" if blockers else "NO_ELIGIBLE_CANDIDATES",
        tuple(sorted(set(blockers))),
        tuple(sorted(exclusions, key=lambda row: row.candidate_id)),
    )


async def select_strategies(invocation: AgentInvocation) -> EligibleStrategySet:
    context = invocation.context
    registry, world = _observations(context)
    receipt = await invocation.tools.call(
        "STRATEGY_REGISTRY_READ", {"registry_sha256": registry.registry_sha256}
    )
    if receipt != {
        "registry_sha256": registry.registry_sha256,
        "candidate_count": len(registry.candidates),
        "status": "CHECKED_REGISTRY_SNAPSHOT",
    }:
        raise ValueError("Selector registry receipt mismatch")
    state, blockers, exclusions = _selection(registry, world, context)
    return EligibleStrategySet(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state=state,
        registry_sha256=registry.registry_sha256,
        world_state_version=world.world_state_version,
        eligible_strategies=(),
        excluded_candidates=exclusions,
        blockers=blockers,
    )


def selector_implementation(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A12", "0.2.0").manifest_ref, EligibleStrategySet, select_strategies
    )


async def select_strategies_v3(invocation: AgentInvocation) -> EligibleStrategySetV3:
    context = invocation.context
    registry, world, performance, _ = _observations_v3(context)
    receipt = await invocation.tools.call(
        "STRATEGY_REGISTRY_READ",
        {
            "registry_sha256": registry.registry_sha256,
            "performance_sha256": performance.fingerprint,
        },
    )
    if receipt != {
        "registry_sha256": registry.registry_sha256,
        "candidate_count": len(registry.candidates),
        "performance_sha256": performance.fingerprint,
        "status": "CHECKED_REGISTRY_AND_SHADOW_SNAPSHOT",
    }:
        raise ValueError("Selector evidence receipt mismatch")
    state, blockers, exclusions = _selection_v3(registry, world, performance, context)
    return EligibleStrategySetV3(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id=str(uuid4()),
        contradictions=(),
        state=state,
        registry_sha256=registry.registry_sha256,
        world_state_version=world.world_state_version,
        eligible_strategies=(),
        excluded_candidates=exclusions,
        blockers=blockers,
    )


def selector_implementation_v3(registry: AgentRegistry) -> AgentImplementation:
    return AgentImplementation(
        registry.get("A12", "0.3.0").manifest_ref,
        EligibleStrategySetV3,
        select_strategies_v3,
    )
