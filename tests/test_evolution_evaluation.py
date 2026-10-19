"""Bounded training diagnostics over exact retained evolution protocols."""

from datetime import datetime, timezone
import json

import pytest

from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_evaluation import (
    CaseOutput,
    EvolutionTournament,
    build_monthly_tournament,
    evaluate_protocol_outputs,
    evolution_output_bundle_schema,
    evolution_scorecard_schema,
    evolution_tournament_schema,
)
from test_evolution_protocols import dataset, protocol
from test_agent_genomes import child


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_exact_training_evaluation_measures_candidate_gain_without_promotion():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)

    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW,
    )

    assert result.primary_metric == "CORRECTNESS"
    assert result.baseline_score == 0
    assert result.challenger_score == 1
    assert result.minimum_improvement_met is True
    assert result.diagnostic == "IMPROVES_ON_TRAINING_DATA"
    assert result.dataset_provenance == "SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT"
    assert result.schema_version == "1.3"
    assert len(result.case_diagnostics) == 1
    assert result.case_diagnostics[0].case_id == "case-1"
    assert result.case_diagnostics[0].baseline_correct is False
    assert result.case_diagnostics[0].challenger_correct is True
    assert result.case_diagnostics[0].weakness_categories == ()
    assert result.evaluation_verified is False
    assert result.promotion_allowed is False


def test_a02_wrong_regime_gets_a_bounded_weakness_category():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"RANGING"}'),),
        evaluated_at=NOW,
    )

    assert result.case_diagnostics[0].weakness_categories == ("REGIME_LABEL_MISMATCH",)
    assert result.evaluation_verified is False
    assert result.promotion_allowed is False


def test_regime_correctness_scores_labels_independently_of_other_output_fields():
    from ai_quant_os.evolution_protocols import EvaluationProtocol

    data = dataset()
    plan_payload = protocol(data).model_dump(mode="json")
    plan_payload["primary_metric"] = "REGIME_CORRECTNESS"
    plan_payload["criteria"][0]["criterion"]["metric"] = "REGIME_CORRECTNESS"
    plan_payload["criteria"][0]["method"] = "REGIME_EXACT_OBJECT_MATCH"
    plan = EvaluationProtocol.model_validate(plan_payload)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (
            CaseOutput(
                case_id="case-1",
                output_json='{"confidence":"0.2","regime":"TRENDING","trace":"old"}',
            ),
        ),
        (
            CaseOutput(
                case_id="case-1",
                output_json='{"confidence":"0.9","regime":"RANGING","trace":"new"}',
            ),
        ),
        evaluated_at=NOW,
    )

    assert result.primary_metric == "REGIME_CORRECTNESS"
    assert result.baseline_exact_matches == 1
    assert result.challenger_exact_matches == 0
    assert result.case_diagnostics[0].baseline_correct is True
    assert result.case_diagnostics[0].challenger_correct is False
    assert result.case_diagnostics[0].weakness_categories == ("REGIME_LABEL_MISMATCH",)
    assert result.diagnostic == "GUARDRAIL_REGRESSION"


def test_regime_correctness_extracts_available_a02_instrument_horizons():
    from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset

    expected = {
        "assessments": [
            {
                "instrument_id": "EURUSD",
                "horizons": [{"horizon": "H1", "status": "AVAILABLE", "regime": "TRENDING"}],
            }
        ]
    }
    data = EvolutionDataset(
        dataset_id="regime-horizon-cases",
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "case-1",
                "input_json": '{"world":"1"}',
                "expected_json": json.dumps(expected, sort_keys=True, separators=(",", ":")),
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="regime-horizon-correctness",
        protocol_version="1.0.0",
        agent_id="A02",
        output_type="RegimeDistribution",
        dataset_ref=data.dataset_ref,
        primary_metric="REGIME_CORRECTNESS",
        criteria=(
            {
                "criterion": {
                    "metric": "REGIME_CORRECTNESS",
                    "direction": "MAXIMIZE",
                    "minimum_improvement": "0",
                },
                "method": "REGIME_EXACT_OBJECT_MATCH",
                "maximum_regression": "0",
            },
        ),
    )
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    baseline_output = {
        "trace_id": "baseline-trace",
        "assessments": expected["assessments"],
    }
    challenger_output = {
        "trace_id": "challenger-trace",
        "confidence": "0",
        "assessments": expected["assessments"],
    }

    def outputs(value):
        return (
            CaseOutput(
                case_id="case-1",
                output_json=json.dumps(value, sort_keys=True, separators=(",", ":")),
            ),
        )

    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        outputs(baseline_output),
        outputs(challenger_output),
        evaluated_at=NOW,
    )

    assert result.baseline_score == result.challenger_score == 1
    assert result.case_diagnostics[0].challenger_correct is True


def test_a07_integrity_status_difference_gets_a_bounded_weakness_category():
    from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset

    data = EvolutionDataset(
        dataset_id="integrity-cases",
        dataset_version="1.0.0",
        agent_id="A07",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "case-1",
                "input_json": '{"world":"1"}',
                "expected_json": '{"status":"HEALTHY"}',
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="integrity-correctness",
        protocol_version="1.0.0",
        agent_id="A07",
        output_type="DataHealth",
        dataset_ref=data.dataset_ref,
        primary_metric="CORRECTNESS",
        criteria=(
            {
                "criterion": {
                    "metric": "CORRECTNESS",
                    "direction": "MAXIMIZE",
                    "minimum_improvement": "0.01",
                },
                "method": "EXACT_OBJECT_MATCH",
                "maximum_regression": "0",
            },
        ),
    )
    baseline = capture_baseline_genome(load_agent_catalog(), "A07", plan.objective)
    challenger = child(baseline)
    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"status":"HEALTHY"}'),),
        (CaseOutput(case_id="case-1", output_json='{"status":"UNUSABLE"}'),),
        evaluated_at=NOW,
    )

    assert result.case_diagnostics[0].weakness_categories == ("INTEGRITY_STATUS_MISMATCH",)


def test_integrity_correctness_ignores_trace_metadata_but_scores_health_evidence():
    from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset
    from ai_quant_os.ingress_health import CODES

    expected = {
        "status": "DEGRADED",
        "instruments": [
            {
                "instrument_id": "EURUSD",
                "usable": False,
                "issues": ["QUOTE_STALE"],
                "affected_features": ["QUOTE"],
            }
        ],
        "quarantined_instruments": ["EURUSD"],
        "ingress_counts": dict.fromkeys(sorted(CODES), 0),
        "clock_status": "UNVERIFIED",
        "contradictions": ["CALIBRATION_UNAVAILABLE"],
    }
    data = EvolutionDataset(
        dataset_id="integrity-adapter-cases",
        dataset_version="1.0.0",
        agent_id="A07",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "case-1",
                "input_json": '{"feed":"stale"}',
                "expected_json": json.dumps(expected, sort_keys=True, separators=(",", ":")),
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="integrity-semantic-correctness",
        protocol_version="1.0.0",
        agent_id="A07",
        output_type="DataHealth",
        dataset_ref=data.dataset_ref,
        primary_metric="INTEGRITY_CORRECTNESS",
        criteria=(
            {
                "criterion": {
                    "metric": "INTEGRITY_CORRECTNESS",
                    "direction": "MAXIMIZE",
                    "minimum_improvement": "0.01",
                },
                "method": "INTEGRITY_EVIDENCE_MATCH",
                "maximum_regression": "0",
            },
        ),
    )
    baseline = capture_baseline_genome(load_agent_catalog(), "A07", plan.objective)
    challenger = child(baseline)
    challenger_evidence = {**expected, "clock_status": "MEASURED"}

    def output(value, trace_id):
        return (
            CaseOutput(
                case_id="case-1",
                output_json=json.dumps(
                    {**value, "trace_id": trace_id}, sort_keys=True, separators=(",", ":")
                ),
            ),
        )

    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        output(expected, "baseline-trace"),
        output(challenger_evidence, "challenger-trace"),
        evaluated_at=NOW,
    )

    assert result.primary_metric == "INTEGRITY_CORRECTNESS"
    assert result.baseline_score == 1
    assert result.challenger_score == 0
    assert result.case_diagnostics[0].baseline_correct is True
    assert result.case_diagnostics[0].challenger_correct is False
    assert result.case_diagnostics[0].weakness_categories == ("CLOCK_EVIDENCE_MISMATCH",)
    assert result.evaluation_verified is False
    assert result.promotion_allowed is False


def test_schema_v1_scorecard_round_trips_with_original_canonical_bytes(tmp_path):
    import json

    from ai_quant_os.evolution_catalog import EvolutionCatalog
    from ai_quant_os.evolution_evaluation import EvolutionScorecard, encode_scorecard

    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    output = CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}')
    current = evaluate_protocol_outputs(
        plan, data, baseline, challenger, (output,), (output,), evaluated_at=NOW
    )
    legacy_payload = current.model_dump(mode="json")
    legacy_payload["schema_version"] = "1.0"
    for row in legacy_payload["case_diagnostics"]:
        row.pop("weakness_categories")
    legacy_bytes = json.dumps(
        legacy_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()

    restored = EvolutionScorecard.model_validate_json(legacy_bytes)

    assert restored.schema_version == "1.0"
    assert encode_scorecard(restored) == legacy_bytes
    catalog = EvolutionCatalog(tmp_path / "legacy-evolution.sqlite3")
    catalog.register(data)
    catalog.register(plan)
    catalog.register(restored)

    reopened = EvolutionCatalog(tmp_path / "legacy-evolution.sqlite3")
    assert reopened.scorecard(restored.scorecard_ref) == restored


@pytest.mark.parametrize("schema_version", ["1.1", "1.2"])
def test_legacy_scorecard_round_trips_with_weakness_categories(schema_version):
    import json

    from ai_quant_os.evolution_evaluation import EvolutionScorecard, encode_scorecard

    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"RANGING"}'),),
        evaluated_at=NOW,
    )
    payload = result.model_dump(mode="json")
    payload["schema_version"] = schema_version
    legacy_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()

    restored = EvolutionScorecard.model_validate_json(legacy_bytes)

    assert restored.schema_version == schema_version
    assert restored.case_diagnostics[0].weakness_categories == ("REGIME_LABEL_MISMATCH",)
    assert encode_scorecard(restored) == legacy_bytes


def test_evaluation_rejects_missing_case_instead_of_scoring_a_subset():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    empty = ()

    with pytest.raises(ValueError, match="exact dataset case inventory"):
        evaluate_protocol_outputs(plan, data, baseline, challenger, empty, empty, evaluated_at=NOW)


def test_evaluation_never_reads_or_scores_holdout_cases():
    data = dataset(resource_class="HOLDOUT")
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    rows = (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),)

    with pytest.raises(ValueError, match="training datasets only"):
        evaluate_protocol_outputs(plan, data, baseline, challenger, rows, rows, evaluated_at=NOW)


def test_evaluation_rejects_candidate_that_is_not_child_of_the_baseline():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    unrelated = child(challenger, version="0.4.0")
    rows = (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),)

    with pytest.raises(ValueError, match="direct challenger"):
        evaluate_protocol_outputs(plan, data, baseline, unrelated, rows, rows, evaluated_at=NOW)


def test_portable_scorecard_schema_matches_runtime_contract():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[1] / "data/schemas/evolution-scorecard.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema == evolution_scorecard_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["evaluation_verified"]["const"] is False
    assert schema["properties"]["promotion_allowed"]["const"] is False


def test_portable_tournament_schema_matches_runtime_contract():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[1] / "data/schemas/evolution-tournament.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema == evolution_tournament_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["evaluation_verified"]["const"] is False
    assert schema["properties"]["promotion_allowed"]["const"] is False


def test_scorecard_case_diagnostics_reconcile_to_exact_match_counts():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW,
    )

    forged = result.model_dump(mode="python")
    forged["case_diagnostics"] = (
        {"case_id": "case-1", "baseline_correct": True, "challenger_correct": True},
    )
    with pytest.raises(ValueError, match="case diagnostics"):
        type(result).model_validate(forged)


def test_monthly_tournament_ranks_retained_training_scorecards_deterministically():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    first = child(baseline)
    second = child(baseline, version="0.4.0")
    worse = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        first,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
        evaluated_at=NOW,
    )
    better = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        second,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW.replace(day=20),
    )

    result = build_monthly_tournament(
        plan,
        (worse, better),
        period="2026-10",
    )

    assert result.protocol_sha256 == plan.protocol_sha256
    assert result.period == "2026-10"
    assert [row.challenger_genome_ref for row in result.rankings] == [
        second.genome_ref,
        first.genome_ref,
    ]
    assert [row.rank for row in result.rankings] == [1, 2]
    assert result.rankings[0].exact_matches == 1
    assert result.promotion_allowed is False
    assert result.evaluation_verified is False
    forged = result.model_dump(mode="python")
    forged["rankings"][0]["exact_matches"] = 2
    with pytest.raises(ValueError, match="unique, complete candidate evidence"):
        EvolutionTournament.model_validate(forged)


def test_monthly_tournament_refuses_mixed_protocols_periods_and_duplicate_challengers():
    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    report = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW,
    )

    with pytest.raises(ValueError, match="period"):
        build_monthly_tournament(plan, (report,), period="2026-09")
    with pytest.raises(ValueError, match="unique challenger"):
        build_monthly_tournament(plan, (report, report), period="2026-10")


def test_monthly_tournament_is_audited_and_reproduces_after_catalog_restart(tmp_path):
    from ai_quant_os.evolution_catalog import EvolutionCatalog

    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    report = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW,
    )
    catalog_path = tmp_path / "evolution.sqlite3"
    catalog = EvolutionCatalog(catalog_path)
    catalog.register(data)
    catalog.register(plan)
    catalog.register(report)

    tournament = catalog.monthly_tournament(plan.protocol_sha256, "2026-10")
    retained = catalog.register(tournament)
    late_challenger = child(baseline, version="0.4.0")
    late_report = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        late_challenger,
        (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="case-1", output_json='{"regime":"OTHER"}'),),
        evaluated_at=NOW.replace(day=21),
    )
    catalog.register(late_report)
    reopened = EvolutionCatalog(catalog_path)

    assert retained == tournament
    assert reopened.tournament(tournament.tournament_ref) == tournament
    assert reopened.monthly_tournament(plan.protocol_sha256, "2026-10") == tournament
    assert sum(row["kind"] == "TOURNAMENT" for row in reopened.inventory()) == 1


def test_portable_output_bundle_schema_matches_runtime_contract():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[1] / "data/schemas/evolution-output-bundle.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema == evolution_output_bundle_schema()
    Draft202012Validator.check_schema(schema)


def test_scorecard_registration_is_audited_idempotent_and_restart_verified(tmp_path):
    from ai_quant_os.evolution_catalog import EvolutionCatalog

    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    output = CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}')
    report = evaluate_protocol_outputs(
        plan, data, baseline, challenger, (output,), (output,), evaluated_at=NOW
    )
    catalog = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    catalog.register(data)
    catalog.register(plan)

    assert catalog.register(report) == report
    assert catalog.register(report) == report
    reopened = EvolutionCatalog(tmp_path / "evaluation.sqlite3")
    assert reopened.scorecard(report.scorecard_ref) == report
    inventory = reopened.inventory()
    assert len(inventory) == 3
    assert sum(row["kind"] == "SCORECARD" for row in inventory) == 1


def test_exact_case_fraction_controls_threshold_at_decimal_precision_boundary():
    from ai_quant_os.evolution_protocols import EvolutionDataset, EvaluationProtocol

    source = dataset().model_dump(mode="json")
    source["cases"] = [
        {
            "case_id": f"case-{index}",
            "input_json": f'{{"price":"{index}"}}',
            "expected_json": '{"regime":"TRENDING"}',
        }
        for index in range(1, 4)
    ]
    data = EvolutionDataset.model_validate(source)
    plan_data = protocol(data).model_dump(mode="json")
    plan_data["criteria"][0]["criterion"]["minimum_improvement"] = "0.33333333333333333333333333333"
    plan = EvaluationProtocol.model_validate(plan_data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    baseline_outputs = tuple(
        CaseOutput(case_id=f"case-{index}", output_json='{"regime":"OTHER"}')
        for index in range(1, 4)
    )
    challenger_outputs = (
        CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),
        CaseOutput(case_id="case-2", output_json='{"regime":"OTHER"}'),
        CaseOutput(case_id="case-3", output_json='{"regime":"OTHER"}'),
    )

    result = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        baseline_outputs,
        challenger_outputs,
        evaluated_at=NOW,
    )

    assert result.minimum_improvement_met is True


@pytest.mark.parametrize("field", ["minimum_improvement_met", "within_guardrail"])
def test_scorecard_boolean_results_reject_numeric_coercion(field):
    from pydantic import ValidationError

    from ai_quant_os.evolution_evaluation import EvolutionScorecard

    data = dataset()
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = child(baseline)
    output = CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}')
    report = evaluate_protocol_outputs(
        plan, data, baseline, challenger, (output,), (output,), evaluated_at=NOW
    )
    payload = report.model_dump()
    if field == "minimum_improvement_met":
        payload[field] = int(payload[field])
    else:
        payload["metrics"][0][field] = int(payload["metrics"][0][field])
    with pytest.raises(ValidationError):
        EvolutionScorecard.model_validate(payload)
