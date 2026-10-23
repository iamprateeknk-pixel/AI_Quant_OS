"""Per-agent evolution coverage audit, derived from verifiable repo facts.

Evolution tournaments can only run for an agent when four surfaces all
exist: a registry manifest, a declared genome source baseline
(:data:`ai_quant_os.agent_sources.AGENT_SOURCE_PATHS`), a sealed-worker
dispatch entry plus output-contract validator
(:mod:`ai_quant_os.evolution_runner`), and dataset/protocol artifacts that
admit the agent (the ``agent_id`` literals in
:mod:`ai_quant_os.evolution_protocols`). Today only A02 and A07 have all
four. Every other agent has a mission entrypoint, but no genome baseline is
declared for it, so claiming tournament coverage for it would be fake.

This module states that coverage explicitly, per agent, from the live
registry and the actual binding points, so the gap list cannot drift
silently: if a surface appears or disappears, the audit output changes.
"""

import importlib
import inspect
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_quant_os.agent_registry import AgentRegistry, load_agent_catalog
from ai_quant_os.agent_sources import AGENT_SOURCE_PATHS

# Mission entrypoints verified present in the tree (module, function). The
# coverage test imports each one and asserts it is a coroutine function, so
# this inventory fails loudly instead of rotting if a mission moves.
MISSION_SURFACES: dict[str, tuple[str, str]] = {
    "A01": ("ai_quant_os.supervisor_agent", "coordinate_specialists"),
    "A02": ("ai_quant_os.regime_agent", "analyze_regimes"),
    "A03": ("ai_quant_os.macro_agent", "assess_macro"),
    "A04": ("ai_quant_os.currency_strength_agent", "assess_currency_strength"),
    "A05": ("ai_quant_os.liquidity_agent", "assess_liquidity"),
    "A06": ("ai_quant_os.event_agent", "assess_events"),
    "A07": ("ai_quant_os.integrity_agent", "assess_integrity"),
    "A08": ("ai_quant_os.research_hypothesis_agent", "generate_hypotheses"),
    "A09": ("ai_quant_os.strategy_builder_agent", "build_strategy"),
    "A10": ("ai_quant_os.validation_scientist_agent", "validate_candidate"),
    "A11": ("ai_quant_os.adversarial_agent", "inspect_adversarial"),
    "A12": ("ai_quant_os.strategy_selector_agent", "select_strategies"),
    "A13": ("ai_quant_os.meta_allocator_agent", "propose_allocation"),
    "A14": ("ai_quant_os.drift_agent", "assess_drift"),
    "A15": ("ai_quant_os.counterfactual_agent", "compare_counterfactuals"),
    "A16": ("ai_quant_os.health_repair_agent", "assess_repair_readiness"),
    "A17": ("ai_quant_os.evolution_director_agent", "plan_evolution"),
    "A18": ("ai_quant_os.incident_agent", "classify_incident"),
}

# Agents the sealed evolution worker can execute and contract-validate.
# Kept in step with the agent literals in evolution_protocols and
# evolution_runner by the coverage test.
RUNNER_SUPPORTED_AGENTS = frozenset({"A02", "A07"})

BLOCKER_NO_GENOME_BASELINE = "NO_DECLARED_GENOME_SOURCE_BASELINE"
BLOCKER_NO_WORKER_SURFACE = "NO_WORKER_DISPATCH_OR_OUTPUT_CONTRACT"
BLOCKER_ARTIFACT_LITERAL = "DATASET_PROTOCOL_AGENT_LITERAL_EXCLUDES_AGENT"
BLOCKER_NO_MISSION = "NO_MISSION_ENTRYPOINT"


class EvolutionCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    name: str = Field(min_length=1, max_length=128)
    output_type: str = Field(min_length=1, max_length=128)
    status: Literal[
        "EVOLUTION_ACTIVE", "MISSION_SURFACE_NOT_EVOLUTION_BOUND", "NO_MISSION_SURFACE"
    ]
    mission_module: str | None = None
    mission_function: str | None = None
    genome_baseline_declared: bool
    runner_supported: bool
    blockers: tuple[str, ...] = Field(max_length=4)


def verify_mission_surface(agent_id: str) -> bool:
    """True only if the recorded mission entrypoint exists and is async."""
    surface = MISSION_SURFACES.get(agent_id)
    if surface is None:
        return False
    module_name, function_name = surface
    try:
        function = getattr(importlib.import_module(module_name), function_name)
    except (ImportError, AttributeError):
        return False
    return inspect.iscoroutinefunction(function)


def build_evolution_coverage(
    registry: AgentRegistry | None = None,
) -> tuple[EvolutionCoverageEntry, ...]:
    """Classify every catalog agent by its genuine evolution readiness."""
    registry = registry if registry is not None else load_agent_catalog()
    entries = []
    for agent_id in sorted(set(MISSION_SURFACES) | {f"A{row:02d}" for row in range(1, 19)}):
        manifest = registry.get(agent_id, "0.2.0")
        surface = MISSION_SURFACES.get(agent_id)
        genome_baseline = (agent_id, "0.2.0") in AGENT_SOURCE_PATHS
        runner_supported = agent_id in RUNNER_SUPPORTED_AGENTS
        blockers: list[str] = []
        if not genome_baseline:
            blockers.append(BLOCKER_NO_GENOME_BASELINE)
        if not runner_supported:
            blockers.append(BLOCKER_NO_WORKER_SURFACE)
            blockers.append(BLOCKER_ARTIFACT_LITERAL)
        if surface is None:
            blockers.append(BLOCKER_NO_MISSION)
        if genome_baseline and runner_supported:
            status = "EVOLUTION_ACTIVE"
        elif surface is not None:
            status = "MISSION_SURFACE_NOT_EVOLUTION_BOUND"
        else:
            status = "NO_MISSION_SURFACE"
        entries.append(
            EvolutionCoverageEntry(
                agent_id=agent_id,
                name=manifest.name,
                output_type=manifest.output_type,
                status=status,
                mission_module=surface[0] if surface else None,
                mission_function=surface[1] if surface else None,
                genome_baseline_declared=genome_baseline,
                runner_supported=runner_supported,
                blockers=tuple(blockers),
            )
        )
    return tuple(entries)
