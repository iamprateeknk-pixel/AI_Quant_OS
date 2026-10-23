"""Evolution coverage audit: only genuinely bound agents claim coverage."""

from typing import get_args

from ai_quant_os.evolution_coverage import (
    MISSION_SURFACES,
    RUNNER_SUPPORTED_AGENTS,
    build_evolution_coverage,
    verify_mission_surface,
)
from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset
from ai_quant_os.evolution_runner import EvolutionExecutionRequest


def test_coverage_covers_every_catalog_agent_exactly_once():
    entries = build_evolution_coverage()
    assert len(entries) == 18
    assert [row.agent_id for row in entries] == [f"A{row:02d}" for row in range(1, 19)]


def test_only_a02_and_a07_are_evolution_active():
    entries = {row.agent_id: row for row in build_evolution_coverage()}
    for agent_id in ("A02", "A07"):
        assert entries[agent_id].status == "EVOLUTION_ACTIVE"
        assert entries[agent_id].genome_baseline_declared is True
        assert entries[agent_id].runner_supported is True
        assert entries[agent_id].blockers == ()
    for agent_id, entry in entries.items():
        if agent_id not in {"A02", "A07"}:
            assert entry.status == "MISSION_SURFACE_NOT_EVOLUTION_BOUND"
            assert entry.genome_baseline_declared is False
            assert entry.runner_supported is False
            assert "NO_DECLARED_GENOME_SOURCE_BASELINE" in entry.blockers
            assert "NO_WORKER_DISPATCH_OR_OUTPUT_CONTRACT" in entry.blockers
            assert "DATASET_PROTOCOL_AGENT_LITERAL_EXCLUDES_AGENT" in entry.blockers


def test_runner_supported_set_matches_the_binding_literals():
    assert set(get_args(EvolutionExecutionRequest.model_fields["agent_id"].annotation)) == set(
        RUNNER_SUPPORTED_AGENTS
    )
    assert set(get_args(EvolutionDataset.model_fields["agent_id"].annotation)) == set(
        RUNNER_SUPPORTED_AGENTS
    )
    assert set(get_args(EvaluationProtocol.model_fields["agent_id"].annotation)) == set(
        RUNNER_SUPPORTED_AGENTS
    )


def test_every_claimed_mission_surface_actually_exists():
    assert set(MISSION_SURFACES) == {f"A{row:02d}" for row in range(1, 19)}
    for agent_id in MISSION_SURFACES:
        assert verify_mission_surface(agent_id), agent_id
