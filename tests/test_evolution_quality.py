from datetime import timedelta

import pytest
import json
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_evaluation import CaseOutput, evaluate_protocol_outputs
from ai_quant_os.evolution_quality import (
    build_training_quality_profile,
    training_quality_profile_schema,
)
from test_evolution_evaluation import NOW
from test_evolution_protocols import dataset, protocol
from test_agent_genomes import child


def _scorecards(resource_class="TRAINING"):
    data = dataset(resource_class=resource_class)
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    return (
        plan,
        data,
        baseline,
        challenger,
        evaluate_protocol_outputs(
            plan,
            data,
            baseline,
            challenger,
            (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        ),
        evaluate_protocol_outputs(
            plan,
            data,
            baseline,
            challenger,
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
            evaluated_at=NOW + timedelta(minutes=1),
        ),
    )


def test_repeated_training_profile_aggregates_exact_results_and_exposes_limits():
    plan, data, baseline, challenger, first, second = _scorecards()

    report = build_training_quality_profile(plan, data, (first, second))

    assert report.candidate_genome_ref == challenger.genome_ref
    assert report.baseline_genome_ref == baseline.genome_ref
    assert report.scorecard_refs == tuple(sorted((first.scorecard_ref, second.scorecard_ref)))
    assert report.case_count == 1
    assert report.observed_case_count == 2
    assert report.baseline_accuracy == report.candidate_accuracy == 0.5
    assert report.stable_candidate_correct_cases == 0
    assert report.variable_candidate_cases == 1
    assert report.improving_runs == 1
    assert report.guardrail_regression_runs == 1
    assert report.evidence_independence == "NOT_ESTABLISHED"
    assert report.evaluation_verified is False
    assert report.promotion_allowed is False


def test_training_quality_profile_requires_repeated_distinct_observations():
    plan, data, _, _, first, _ = _scorecards()
    with pytest.raises(ValueError, match="at least two"):
        build_training_quality_profile(plan, data, (first,))
    with pytest.raises(ValueError, match="distinct evaluation times"):
        build_training_quality_profile(plan, data, (first, first))


def test_training_quality_profile_refuses_holdout_scorecards():
    _, _, _, _, first, second = _scorecards()
    holdout_data = dataset(resource_class="HOLDOUT")
    holdout_plan = protocol(holdout_data)
    with pytest.raises(ValueError, match="TRAINING"):
        build_training_quality_profile(holdout_plan, holdout_data, (first, second))


def test_profile_model_rejects_claims_of_independent_verification_or_promotion():
    plan, data, _, _, first, second = _scorecards()
    report = build_training_quality_profile(plan, data, (first, second))
    payload = report.model_dump()
    payload["promotion_allowed"] = True
    with pytest.raises(ValidationError):
        type(report).model_validate(payload)


def test_catalog_rebuilds_and_retains_a_hash_bound_quality_profile(tmp_path):
    from ai_quant_os.evolution_catalog import EvolutionCatalog

    plan, data, _, challenger, first, second = _scorecards()
    catalog = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    catalog.register(first)
    catalog.register(second)

    report = catalog.training_quality_profile(plan.protocol_sha256, challenger.genome_ref)
    retained = catalog.register(report)
    reopened = EvolutionCatalog(tmp_path / "evaluation.sqlite3")

    assert retained == report
    assert reopened.quality_profile(report.quality_profile_ref) == report
    assert any(row["kind"] == "QUALITY_PROFILE" for row in reopened.inventory())


def test_portable_training_quality_schema_matches_runtime_contract():
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "data/schemas/evolution-training-quality-profile.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema == training_quality_profile_schema()
    Draft202012Validator.check_schema(schema)


def test_quality_profile_cli_persists_repeatability_summary_without_authority(tmp_path):
    from test_evolution_cli import run

    plan, data, _, challenger, first, second = _scorecards()
    workspace = tmp_path / "evolution"
    catalog = __import__("ai_quant_os.evolution_catalog", fromlist=["EvolutionCatalog"])
    retained = catalog.EvolutionCatalog(workspace / "evaluation.sqlite3")
    for artifact in (data, plan, first, second):
        retained.register(artifact)

    result = run(
        workspace,
        "quality-profile",
        "--protocol",
        plan.protocol_sha256,
        "--challenger-genome",
        challenger.genome_ref,
    )

    assert result.returncode == 0, result.stderr.decode()
    report = json.loads(result.stdout)
    assert report["candidate_accuracy"] == "0.5"
    assert report["variable_candidate_cases"] == 1
    assert report["evaluation_verified"] is False
    assert report["promotion_allowed"] is False
    assert report["evidence_independence"] == "NOT_ESTABLISHED"
    assert any(row["kind"] == "QUALITY_PROFILE" for row in retained.inventory())
