from datetime import datetime, timedelta, timezone
import json

import pytest

from ai_quant_os.agent_context import (
    CompiledContext,
    ContextInput,
    EvidenceRecord,
    _context_ref,
    verify_context,
)
from ai_quant_os.agent_genomes import capture_baseline_genome
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.evolution_protocols import EvaluationProtocol, EvolutionDataset
from ai_quant_os.evolution_runner import (
    EvolutionRunner,
    _unique_object,
    build_execution_request,
)
from test_evolution_protocols import protocol as make_protocol


def _context(resource_class="TRAINING"):
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
        task_id="training-case",
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


def _request(resource_class="TRAINING"):
    plan_data = __import__("test_evolution_protocols").dataset(
        resource_class="HOLDOUT" if resource_class == "HOLDOUT" else "TRAINING",
        input_json=json.dumps(
            _context(resource_class).model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ),
    )
    plan = make_protocol(plan_data)
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    return build_execution_request(
        genome=baseline, dataset=plan_data, protocol=plan, contexts=(_context(resource_class),)
    )


def _training_context(agent_id, payloads, now):
    manifest = load_agent_catalog().get(agent_id, "0.2.0")
    source_tool = "WORLD_STATE_READ" if agent_id == "A02" else "HEALTH_READ"
    inputs = []
    for name in manifest.required_inputs:
        record = EvidenceRecord(
            source_id="synthetic-training-" + name,
            as_of=now,
            valid_until=now + timedelta(seconds=10),
            trusted=True,
            payload_json=json.dumps(payloads[name], sort_keys=True, separators=(",", ":")),
        )
        inputs.append(
            ContextInput(
                input_name=name,
                status="AVAILABLE",
                resource_class="TRAINING",
                source_tool=source_tool,
                source_id=record.source_id,
                as_of=record.as_of,
                valid_until=record.valid_until,
                content_ref=record.content_ref,
                payload_json=record.payload_json,
            )
        )
    context = CompiledContext(
        context_ref="pending",
        agent_id=agent_id,
        agent_version="0.2.0",
        manifest_ref=manifest.manifest_ref,
        task_id="evolution-probe",
        scope="SANDBOX",
        status="READY",
        inputs=tuple(inputs),
        input_refs=tuple(row.content_ref for row in inputs),
        created_at=now,
        valid_until=now + timedelta(seconds=5),
    )
    context = context.model_copy(update={"context_ref": _context_ref(context)})
    verify_context(context)
    return context


def test_runner_request_rejects_holdout_before_container_creation():
    with pytest.raises(ValueError, match="training dataset"):
        _request("HOLDOUT")


def test_runner_request_rejects_context_without_training_only_evidence():
    with pytest.raises(ValueError, match="training-only"):
        _request("GENERAL")


def test_worker_json_parser_rejects_duplicate_object_keys():
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads(
            '{"execution_verified":false,"execution_verified":true}',
            object_pairs_hook=_unique_object,
        )


@pytest.mark.skipif(
    __import__("os").environ.get("AI_QUANT_EVOLUTION_RUNNER_ENGINE_TEST") != "1",
    reason="requires the pinned local Linux Docker worker image",
)
def test_pinned_container_executes_baseline_and_genome_sources_without_promotion(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    world = {
        "schema_version": "1.0",
        "world_state_version": "probe-world",
        "as_of": now.isoformat(),
        "valid_until": (now + timedelta(seconds=5)).isoformat(),
        "status": "READY",
        "instruments": [],
        "currency_twins": [],
        "policy_version": "paper-regime-v1",
        "source": "PAPER_SIMULATED",
    }
    payloads = {
        "world_state": world,
        "price_features": {"world_state_version": "probe-world", "features": []},
        "volatility_features": {"world_state_version": "probe-world", "features": []},
        "session_state": {
            "world_state_version": "probe-world",
            "clock_basis": "UTC",
            "as_of": now.isoformat(),
            "calendar_status": "UNAVAILABLE",
            "event_status": "UNAVAILABLE",
            "crisis_status": "UNAVAILABLE",
        },
    }
    context = _training_context("A02", payloads, now)
    data = EvolutionDataset(
        dataset_id="container-runner-smoke",
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "probe-1",
                "input_json": json.dumps(
                    context.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ),
                "expected_json": "{}",
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="container-runner-smoke",
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
                    "minimum_improvement": "0.01",
                },
                "method": "REGIME_EXACT_OBJECT_MATCH",
                "maximum_regression": "0",
            },
        ),
    )
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    first = EvolutionRunner().run(baseline, data, plan)
    assert first.outputs[0]["output"]["agent_version"] == "0.2.0"
    assert first.promotion_allowed is False and first.execution_verified is False

    child_payload = baseline.model_dump(mode="json")
    child_payload["parent_ref"] = baseline.genome_ref
    child_payload["manifest"]["agent_version"] = "0.3.0"
    for source in child_payload["sources"]:
        if source["path"].endswith("regime_agent.py"):
            source["content"] = source["content"].replace('"0.2.0"', '"0.3.0"')
            source["content"] += "\n# isolated runner smoke candidate\n"
    from ai_quant_os.agent_genomes import AgentGenome

    child = AgentGenome.model_validate(child_payload)
    second = EvolutionRunner().run(child, data, plan, baseline=baseline)
    assert second.outputs[0]["output"]["agent_version"] == "0.3.0"
    assert second.outputs[0]["output"]["assessments"] == first.outputs[0]["output"]["assessments"]
    assert second.promotion_allowed is False and second.execution_verified is False


@pytest.mark.skipif(
    __import__("os").environ.get("AI_QUANT_EVOLUTION_RUNNER_ENGINE_TEST") != "1",
    reason="requires the pinned local Linux Docker worker image",
)
def test_pinned_container_executes_a07_baseline_and_source_overlay():
    from ai_quant_os.agent_genomes import AgentGenome
    from ai_quant_os.ingress_health import CODES

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payloads = {
        "feed_health": {
            "world_state_version": "probe-world",
            "feeds": [
                {
                    "instrument_id": "EURUSD",
                    "quote_status": "FRESH",
                    "history_status": "FRESH",
                }
            ],
            "ingress_counts": {code: 0 for code in CODES},
            "retention": "PROCESS_LOCAL_SINCE_STARTUP",
        },
        "lineage": {
            "world_state_version": "probe-world",
            "verified_instruments": ["EURUSD"],
            "coverage": "LOCAL_BAR_CONTENT_HASH_ONLY",
        },
        "clock_health": {
            "world_state_version": "probe-world",
            "status": "UNVERIFIED",
            "offset_ms": None,
        },
    }
    context = _training_context("A07", payloads, now)
    data = EvolutionDataset(
        dataset_id="a07-container-runner-smoke",
        dataset_version="1.0.0",
        agent_id="A07",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "probe-1",
                "input_json": json.dumps(
                    context.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ),
                "expected_json": "{}",
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="a07-container-runner-smoke",
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
    first = EvolutionRunner().run(baseline, data, plan)
    child_payload = baseline.model_dump(mode="json")
    child_payload["parent_ref"] = baseline.genome_ref
    child_payload["manifest"]["agent_version"] = "0.3.0"
    child_payload["sources"][0]["content"] = (
        child_payload["sources"][0]["content"].replace('"0.2.0"', '"0.3.0"')
        + "\n# isolated runner smoke candidate\n"
    )
    child = AgentGenome.model_validate(child_payload)
    second = EvolutionRunner().run(child, data, plan, baseline=baseline)
    assert first.outputs[0]["output"]["status"] == "DEGRADED"
    assert second.outputs[0]["output"]["agent_version"] == "0.3.0"
    assert second.outputs[0]["output"]["instruments"] == first.outputs[0]["output"]["instruments"]
    assert second.promotion_allowed is False and second.execution_verified is False


@pytest.mark.skipif(
    __import__("os").environ.get("AI_QUANT_EVOLUTION_RUNNER_ENGINE_TEST") != "1",
    reason="requires the pinned local Linux Docker worker image",
)
def test_pinned_runner_detects_predeclared_a07_false_healthy_mutation():
    from ai_quant_os.evolution_evaluation import CaseOutput, evaluate_protocol_outputs
    from ai_quant_os.evolution_mutations import build_mutation
    from ai_quant_os.ingress_health import CODES

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payloads = {
        "feed_health": {
            "world_state_version": "mutation-world",
            "feeds": [
                {
                    "instrument_id": "EURUSD",
                    "quote_status": "STALE",
                    "history_status": "FRESH",
                }
            ],
            "ingress_counts": {code: 0 for code in CODES},
            "retention": "PROCESS_LOCAL_SINCE_STARTUP",
        },
        "lineage": {
            "world_state_version": "mutation-world",
            "verified_instruments": ["EURUSD"],
            "coverage": "LOCAL_BAR_CONTENT_HASH_ONLY",
        },
        "clock_health": {
            "world_state_version": "mutation-world",
            "status": "UNVERIFIED",
            "offset_ms": None,
        },
    }
    stale_context = _training_context("A07", payloads, now)
    stale_expected = {
        "status": "UNUSABLE",
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
        "contradictions": [
            "CALIBRATION_UNAVAILABLE",
            "CLOCK_MEASUREMENT_UNAVAILABLE",
            "EXTERNAL_LINEAGE_UNVERIFIED",
        ],
    }
    healthy_payloads = json.loads(json.dumps(payloads))
    healthy_payloads["feed_health"]["feeds"][0]["quote_status"] = "FRESH"
    healthy_context = _training_context("A07", healthy_payloads, now)
    healthy_expected = {
        "status": "DEGRADED",
        "instruments": [
            {
                "instrument_id": "EURUSD",
                "usable": True,
                "issues": [],
                "affected_features": [],
            }
        ],
        "quarantined_instruments": [],
        "ingress_counts": dict.fromkeys(sorted(CODES), 0),
        "clock_status": "UNVERIFIED",
        "contradictions": [
            "CALIBRATION_UNAVAILABLE",
            "CLOCK_MEASUREMENT_UNAVAILABLE",
            "EXTERNAL_LINEAGE_UNVERIFIED",
        ],
    }
    data = EvolutionDataset(
        dataset_id="a07-known-false-healthy-mutation",
        dataset_version="1.0.0",
        agent_id="A07",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "fresh-inputs-preserve-degraded-status",
                "input_json": json.dumps(
                    healthy_context.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "expected_json": json.dumps(
                    healthy_expected, sort_keys=True, separators=(",", ":")
                ),
            },
            {
                "case_id": "stale-quote-is-unusable",
                "input_json": json.dumps(
                    stale_context.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "expected_json": json.dumps(stale_expected, sort_keys=True, separators=(",", ":")),
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="a07-known-false-healthy-mutation",
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
    challenger = build_mutation(baseline, "A07_FORCE_HEALTHY_STATUS")
    baseline_result = EvolutionRunner().run(baseline, data, plan)
    challenger_result = EvolutionRunner().run(challenger, data, plan, baseline=baseline)

    def case_outputs(result):
        return CaseOutput(
            case_id=result.outputs[0]["case_id"],
            output_json=json.dumps(
                result.outputs[0]["output"], sort_keys=True, separators=(",", ":")
            ),
        ), CaseOutput(
            case_id=result.outputs[1]["case_id"],
            output_json=json.dumps(
                result.outputs[1]["output"], sort_keys=True, separators=(",", ":")
            ),
        )

    scorecard = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        case_outputs(baseline_result),
        case_outputs(challenger_result),
        evaluated_at=now,
    )

    assert baseline_result.outputs[0]["output"]["status"] == "DEGRADED"
    assert baseline_result.outputs[1]["output"]["status"] == "UNUSABLE"
    assert challenger_result.outputs[0]["output"]["status"] == "DEGRADED"
    assert challenger_result.outputs[1]["output"]["status"] == "HEALTHY"
    assert scorecard.baseline_score == 1
    assert scorecard.challenger_score == 0.5
    assert scorecard.diagnostic == "GUARDRAIL_REGRESSION"
    assert scorecard.case_diagnostics[0].weakness_categories == ()
    assert scorecard.case_diagnostics[1].weakness_categories == ("INTEGRITY_STATUS_MISMATCH",)
    assert scorecard.evaluation_verified is False
    assert scorecard.promotion_allowed is False


@pytest.mark.skipif(
    __import__("os").environ.get("AI_QUANT_EVOLUTION_RUNNER_ENGINE_TEST") != "1",
    reason="requires the pinned local Linux Docker worker image",
)
def test_pinned_runner_detects_predeclared_a02_wrong_regime_mutation():
    import asyncio
    from decimal import Decimal

    from ai_quant_os.agent_context import ContextInput
    from ai_quant_os.evolution_evaluation import CaseOutput, evaluate_protocol_outputs
    from ai_quant_os.evolution_mutations import build_mutation
    from ai_quant_os.graph_runtime import AgentInvocation
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.market_data import Bar
    from ai_quant_os.paper_context_sources import paper_context_sources
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.regime_agent import analyze_regimes
    from ai_quant_os.world_state import WorldStateEngine

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    manifest = load_agent_catalog().get("A02", "0.2.0")
    quote = MarketSnapshot(
        "EURUSD", Decimal("1.0630"), Decimal("1.0631"), now, "mutation-world", True
    )

    def context_for(regime, task_id):
        engine = WorldStateEngine(PAPER_INSTRUMENTS)
        bars = tuple(
            Bar(
                "EURUSD",
                now - timedelta(seconds=63 - index),
                Decimal("1") + (Decimal("0.001") * index if regime == "TRENDING" else 0),
                Decimal("1") + (Decimal("0.001") * index if regime == "TRENDING" else 0),
                Decimal("1") + (Decimal("0.001") * index if regime == "TRENDING" else 0),
                Decimal("1") + (Decimal("0.001") * index if regime == "TRENDING" else 0),
                Decimal(1),
            )
            for index in range(64)
        )
        engine.record_bars(bars, now=now)
        snapshot = engine.snapshot((quote,), now=now)
        sources = paper_context_sources(snapshot, engine.histories())
        inputs = []
        for name in manifest.required_inputs:
            source = sources[name]
            record = asyncio.run(source.fetch())
            inputs.append(
                ContextInput(
                    input_name=name,
                    status="AVAILABLE",
                    resource_class="TRAINING",
                    source_tool=source.tool,
                    source_id=record.source_id,
                    as_of=record.as_of,
                    valid_until=record.valid_until,
                    content_ref=record.content_ref,
                    payload_json=record.payload_json,
                )
            )
        value = CompiledContext(
            context_ref="pending",
            agent_id="A02",
            agent_version="0.2.0",
            manifest_ref=manifest.manifest_ref,
            task_id=task_id,
            scope="SANDBOX",
            status="READY",
            inputs=tuple(inputs),
            input_refs=tuple(row.content_ref for row in inputs),
            created_at=now,
            valid_until=now + timedelta(seconds=5),
        )
        value = value.model_copy(update={"context_ref": _context_ref(value)})
        verify_context(value)
        expected = asyncio.run(analyze_regimes(AgentInvocation(value, None))).model_dump(
            mode="json"
        )
        return value, expected

    trending_context, trending_expected = context_for("TRENDING", "a02-trending-mutation")
    ranging_context, ranging_expected = context_for("RANGING", "a02-ranging-mutation")
    data = EvolutionDataset(
        dataset_id="a02-known-wrong-regime-mutation",
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class="TRAINING",
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=(
            {
                "case_id": "available-range-remains-ranging",
                "input_json": json.dumps(
                    ranging_context.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "expected_json": json.dumps(
                    ranging_expected, sort_keys=True, separators=(",", ":")
                ),
            },
            {
                "case_id": "available-trend-is-forced-unknown",
                "input_json": json.dumps(
                    trending_context.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "expected_json": json.dumps(
                    trending_expected, sort_keys=True, separators=(",", ":")
                ),
            },
        ),
    )
    plan = EvaluationProtocol(
        protocol_id="a02-known-wrong-regime-mutation",
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
                    "minimum_improvement": "0.01",
                },
                "method": "REGIME_EXACT_OBJECT_MATCH",
                "maximum_regression": "0",
            },
        ),
    )
    baseline = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    challenger = build_mutation(baseline, "A02_FORCE_UNKNOWN_REGIME")
    baseline_result = EvolutionRunner().run(baseline, data, plan)
    challenger_result = EvolutionRunner().run(challenger, data, plan, baseline=baseline)

    def case_outputs(result):
        return tuple(
            CaseOutput(
                case_id=row["case_id"],
                output_json=json.dumps(row["output"], sort_keys=True, separators=(",", ":")),
            )
            for row in result.outputs
        )

    scorecard = evaluate_protocol_outputs(
        plan,
        data,
        baseline,
        challenger,
        case_outputs(baseline_result),
        case_outputs(challenger_result),
        evaluated_at=now,
    )

    by_case = {row["case_id"]: row["output"] for row in baseline_result.outputs}
    challenger_by_case = {row["case_id"]: row["output"] for row in challenger_result.outputs}
    assert (
        by_case["available-trend-is-forced-unknown"]["assessments"][0]["horizons"][0]["regime"]
        == "TRENDING"
    )
    assert (
        by_case["available-range-remains-ranging"]["assessments"][0]["horizons"][0]["regime"]
        == "RANGING"
    )
    assert (
        challenger_by_case["available-trend-is-forced-unknown"]["assessments"][0]["horizons"][0][
            "regime"
        ]
        == "UNKNOWN"
    )
    assert (
        challenger_by_case["available-range-remains-ranging"]["assessments"][0]["horizons"][0][
            "regime"
        ]
        == "RANGING"
    )
    assert scorecard.baseline_score == 1
    assert scorecard.challenger_score == 0.5
    assert scorecard.diagnostic == "GUARDRAIL_REGRESSION"
    assert scorecard.case_diagnostics[0].weakness_categories == ()
    assert scorecard.case_diagnostics[1].weakness_categories == ("REGIME_LABEL_MISMATCH",)
    assert scorecard.evaluation_verified is False
    assert scorecard.promotion_allowed is False
