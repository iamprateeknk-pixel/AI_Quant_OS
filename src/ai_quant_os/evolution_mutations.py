"""Closed training-only behavioral mutations for runner sensitivity checks."""

from dataclasses import dataclass

from ai_quant_os.agent_genomes import (
    AgentGenome,
    GenomeSource,
    capture_baseline_genome,
    require_genome_child,
)
from ai_quant_os.agent_registry import load_agent_catalog


@dataclass(frozen=True)
class PredeclaredMutation:
    mutation_id: str
    agent_id: str
    source_path: str
    expected_snippet: str
    replacement_snippet: str
    weakness_category: str


PREDECLARED_MUTATIONS = (
    PredeclaredMutation(
        mutation_id="A02_FORCE_UNKNOWN_REGIME",
        agent_id="A02",
        source_path="src/ai_quant_os/regime_agent.py",
        expected_snippet="label, scores = regime_scores(feature)",
        replacement_snippet=(
            "label, scores = regime_scores(feature)\n"
            "            if (\n"
            '                feature.status == "AVAILABLE"\n'
            "                and feature.return_bps is not None\n"
            "                and feature.return_bps > 0\n"
            "            ):\n"
            '                label = "UNKNOWN"'
        ),
        weakness_category="REGIME_LABEL_MISMATCH",
    ),
    PredeclaredMutation(
        mutation_id="A07_FORCE_HEALTHY_STATUS",
        agent_id="A07",
        source_path="src/ai_quant_os/integrity_agent.py",
        expected_snippet=(
            'status="UNUSABLE" if len(quarantined) == len(findings) else "DEGRADED",'
        ),
        replacement_snippet=(
            'status="HEALTHY" if quarantined else (\n'
            '            "UNUSABLE" if len(quarantined) == len(findings) else "DEGRADED"\n'
            "        ),"
        ),
        weakness_category="INTEGRITY_STATUS_MISMATCH",
    ),
)


def build_mutation(baseline: AgentGenome, mutation_id: str) -> AgentGenome:
    """Create one fixed source mutation as a direct child of its exact captured root."""
    mutation = next((row for row in PREDECLARED_MUTATIONS if row.mutation_id == mutation_id), None)
    if mutation is None:
        raise ValueError("unknown mutation ID")
    if type(baseline) is not AgentGenome or baseline.parent_ref is not None:
        raise ValueError("Evolution mutation requires a captured baseline genome")
    expected = capture_baseline_genome(load_agent_catalog(), mutation.agent_id, baseline.objective)
    if baseline != expected:
        raise ValueError("Evolution mutation requires the exact server-owned baseline")
    target_count = sum(source.path == mutation.source_path for source in baseline.sources)
    if target_count != 1:
        raise ValueError("Evolution mutation target is outside its source ownership")

    sources = []
    for source in baseline.sources:
        if source.path == mutation.source_path:
            if source.content.count(mutation.expected_snippet) != 1:
                raise ValueError("Evolution mutation source anchor is not unique")
            content = source.content.replace('"0.2.0"', '"0.3.0"').replace(
                mutation.expected_snippet, mutation.replacement_snippet, 1
            )
            if content == source.content:
                raise ValueError("Evolution mutation made no source change")
            sources.append(GenomeSource(path=source.path, content=content))
        else:
            sources.append(source)

    child = AgentGenome(
        manifest=baseline.manifest.model_copy(update={"agent_version": "0.3.0"}),
        sources=tuple(sources),
        parent_ref=baseline.genome_ref,
        objective=baseline.objective,
        model_route=baseline.model_route,
        model_ref=baseline.model_ref,
        prompt_template=baseline.prompt_template,
        feature_refs=baseline.feature_refs,
    )
    require_genome_child(baseline, child)
    return child
