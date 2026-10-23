"""Independent holdout evaluation: unseen data, same ruler, fail-closed."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from ai_quant_os.agent_context import (
    CompiledContext,
    ContextInput,
    EvidenceRecord,
    _context_ref,
)
from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_evaluation import CaseOutput
from ai_quant_os.evolution_holdout import evaluate_holdout_outputs
from ai_quant_os.evolution_protocols import EvolutionDataset
from ai_quant_os.evolution_runner import (
    EvolutionRunner,
    build_execution_request,
    build_holdout_execution_request,
)
from test_agent_genomes import child
from test_evolution_protocols import dataset, protocol

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def holdout_dataset(
    *,
    provenance="REVIEWED_REFERENCE",
    case_id="holdout-1",
    input_json='{"price":"9"}',
    expected_json='{"regime":"TRENDING"}',
):
    return EvolutionDataset(
        dataset_id="regime-holdout",
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class="HOLDOUT",
        provenance=provenance,
        cases=[{"case_id": case_id, "input_json": input_json, "expected_json": expected_json}],
    )


def _pair():
    training = dataset()
    training_protocol = protocol(training)
    holdout = holdout_dataset()
    holdout_protocol = protocol(holdout)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", training_protocol.objective)
    return training, training_protocol, holdout, holdout_protocol, baseline, child(baseline)


def test_holdout_pass_when_challenger_improves_on_unseen_data():
    training, training_protocol, holdout, holdout_protocol, baseline, challenger = _pair()
    result = evaluate_holdout_outputs(
        training_protocol,
        holdout_protocol,
        training,
        holdout,
        baseline,
        challenger,
        (CaseOutput(case_id="holdout-1", output_json='{"regime":"OTHER"}'),),
        (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
        evaluated_at=NOW,
    )
    assert result.verdict == "HOLDOUT_PASS"
    assert result.baseline_score == 0
    assert result.challenger_score == 1
    assert result.minimum_improvement_met is True
    assert result.input_overlap_verified_disjoint is True
    assert result.holdout_dataset_provenance == "REVIEWED_REFERENCE"
    assert result.evaluation_ref.startswith("evolution-holdout:")
    assert result.execution_verified is False
    assert result.promotion_allowed is False


def test_holdout_fail_when_challenger_does_not_improve_on_unseen_data():
    training, training_protocol, holdout, holdout_protocol, baseline, challenger = _pair()
    result = evaluate_holdout_outputs(
        training_protocol,
        holdout_protocol,
        training,
        holdout,
        baseline,
        challenger,
        (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
        (CaseOutput(case_id="holdout-1", output_json='{"regime":"OTHER"}'),),
        evaluated_at=NOW,
    )
    assert result.verdict == "HOLDOUT_FAIL"
    assert result.minimum_improvement_met is False


def test_holdout_refuses_training_classed_dataset():
    training, training_protocol, holdout, holdout_protocol, baseline, challenger = _pair()
    with pytest.raises(ValueError, match="holdout datasets only"):
        evaluate_holdout_outputs(
            training_protocol,
            training_protocol,
            training,
            training,
            baseline,
            challenger,
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="case-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        )


def test_holdout_refuses_synthetic_development_provenance():
    training, training_protocol, _, _, baseline, challenger = _pair()
    synthetic = holdout_dataset(provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT")
    with pytest.raises(ValueError, match="Synthetic development data"):
        evaluate_holdout_outputs(
            training_protocol,
            protocol(synthetic),
            training,
            synthetic,
            baseline,
            challenger,
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        )


def test_holdout_refuses_inputs_that_overlap_training():
    training, training_protocol, _, _, baseline, challenger = _pair()
    leaked = holdout_dataset(case_id="renamed-1", input_json='{"price":"1"}')
    with pytest.raises(ValueError, match="overlap"):
        evaluate_holdout_outputs(
            training_protocol,
            protocol(leaked),
            training,
            leaked,
            baseline,
            challenger,
            (CaseOutput(case_id="renamed-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="renamed-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        )


def test_holdout_refuses_a_different_ruler_than_training():
    training, training_protocol, holdout, _, baseline, challenger = _pair()
    payload = protocol(holdout).model_dump(mode="json")
    payload["criteria"][0]["criterion"]["minimum_improvement"] = "0.5"
    from ai_quant_os.evolution_protocols import EvaluationProtocol

    stricter = EvaluationProtocol.model_validate(payload)
    with pytest.raises(ValueError, match="criteria"):
        evaluate_holdout_outputs(
            training_protocol,
            stricter,
            training,
            holdout,
            baseline,
            challenger,
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        )


def test_holdout_refuses_a_challenger_that_is_not_a_direct_child():
    training, training_protocol, holdout, holdout_protocol, baseline, _ = _pair()
    with pytest.raises(ValueError, match="direct challenger"):
        evaluate_holdout_outputs(
            training_protocol,
            holdout_protocol,
            training,
            holdout,
            baseline,
            baseline,
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            (CaseOutput(case_id="holdout-1", output_json='{"regime":"TRENDING"}'),),
            evaluated_at=NOW,
        )


def _context(resource_class="HOLDOUT"):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    record = EvidenceRecord(
        source_id="fixture",
        as_of=now,
        valid_until=now + timedelta(seconds=10),
        trusted=True,
        payload_json="{}",
    )
    context = CompiledContext(
        context_ref="pending",
        agent_id="A02",
        agent_version="0.2.0",
        manifest_ref="agent-manifest:" + "a" * 64,
        task_id="holdout-case",
        scope="SANDBOX",
        status="READY",
        inputs=(
            ContextInput(
                input_name="world_state",
                status="AVAILABLE",
                resource_class=resource_class,
                source_tool="WORLD_STATE_READ",
                source_id="fixture",
                as_of=now,
                valid_until=now + timedelta(seconds=10),
                content_ref=record.content_ref,
                payload_json="{}",
            ),
        ),
        input_refs=(record.content_ref,),
        created_at=now,
        valid_until=now + timedelta(seconds=5),
    )
    return context.model_copy(update={"context_ref": _context_ref(context)})


def _holdout_request(resource_class="HOLDOUT"):
    context = _context(resource_class)
    data = EvolutionDataset(
        dataset_id="holdout-context-cases",
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class="HOLDOUT",
        provenance="REVIEWED_REFERENCE",
        cases=[
            {
                "case_id": "case-1",
                "input_json": json.dumps(
                    context.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ),
                "expected_json": '{"regime":"TRENDING"}',
            }
        ],
    )
    plan = protocol(data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    return baseline, data, plan, context


def test_holdout_request_binds_holdout_contexts():
    baseline, data, plan, context = _holdout_request()
    request = build_holdout_execution_request(
        genome=baseline, dataset=data, protocol=plan, contexts=(context,)
    )
    assert request.agent_id == "A02"
    assert request.dataset_ref == data.dataset_ref


def test_holdout_request_rejects_training_dataset_and_contexts():
    baseline, data, plan, context = _holdout_request()
    with pytest.raises(ValueError, match="holdout dataset only"):
        build_holdout_execution_request(
            genome=baseline,
            dataset=dataset(resource_class="TRAINING"),
            protocol=plan,
            contexts=(context,),
        )
    leaked_baseline, leaked_data, leaked_plan, leaked_context = _holdout_request("TRAINING")
    with pytest.raises(ValueError, match="holdout-only"):
        build_holdout_execution_request(
            genome=leaked_baseline,
            dataset=leaked_data,
            protocol=leaked_plan,
            contexts=(leaked_context,),
        )
    with pytest.raises(ValueError, match="training dataset"):
        build_execution_request(genome=baseline, dataset=data, protocol=plan, contexts=(context,))


def test_run_holdout_enforces_lineage_before_any_container_work():
    baseline, data, plan, _ = _holdout_request()
    challenger = child(baseline)
    with pytest.raises(ValueError, match="exact parent baseline"):
        EvolutionRunner().run_holdout(challenger, data, plan)
    with pytest.raises(ValueError, match="server-owned baseline"):
        EvolutionRunner().run_holdout(baseline, data, plan, baseline=baseline)
