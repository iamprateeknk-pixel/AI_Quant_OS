import pytest

from ai_quant_os.agent_genomes import require_genome_child
from ai_quant_os.evolution_mutations import PREDECLARED_MUTATIONS, build_mutation
from test_agent_genomes import baseline


@pytest.mark.parametrize(
    ("mutation_id", "agent_id", "source_path", "original", "replacement"),
    [
        (
            "A02_FORCE_UNKNOWN_REGIME",
            "A02",
            "src/ai_quant_os/regime_agent.py",
            "label, scores = regime_scores(feature)",
            "label, scores = regime_scores(feature)\n"
            "            if (\n"
            '                feature.status == "AVAILABLE"\n'
            "                and feature.return_bps is not None\n"
            "                and feature.return_bps > 0\n"
            "            ):\n"
            '                label = "UNKNOWN"',
        ),
        (
            "A07_FORCE_HEALTHY_STATUS",
            "A07",
            "src/ai_quant_os/integrity_agent.py",
            'status="UNUSABLE" if len(quarantined) == len(findings) else "DEGRADED",',
            'status="HEALTHY" if quarantined else (\n'
            '            "UNUSABLE" if len(quarantined) == len(findings) else "DEGRADED"\n'
            "        ),",
        ),
    ],
)
def test_predeclared_behavior_mutation_is_one_exact_unverified_child(
    mutation_id, agent_id, source_path, original, replacement
):
    parent = baseline(agent_id)

    child = build_mutation(parent, mutation_id)

    require_genome_child(parent, child)
    assert child.manifest.agent_version == "0.3.0"
    assert child.execution_verified is False and child.promotion_allowed is False
    assert child.objective == parent.objective
    assert tuple(row.path for row in child.sources) == tuple(row.path for row in parent.sources)
    for before, after in zip(parent.sources, child.sources, strict=True):
        if before.path == source_path:
            assert before.content.count(original) == 1
            assert after.content.count(replacement) == 1
            assert after.content != before.content
        else:
            assert after == before


def test_mutation_catalog_is_closed_and_rejects_unknown_or_nonroot_inputs():
    assert tuple(row.mutation_id for row in PREDECLARED_MUTATIONS) == (
        "A02_FORCE_UNKNOWN_REGIME",
        "A07_FORCE_HEALTHY_STATUS",
    )
    parent = baseline("A02")
    with pytest.raises(ValueError, match="unknown mutation"):
        build_mutation(parent, "CALLER_SUPPLIED_SOURCE")
    child = build_mutation(parent, "A02_FORCE_UNKNOWN_REGIME")
    with pytest.raises(ValueError, match="baseline"):
        build_mutation(child, "A02_FORCE_UNKNOWN_REGIME")
