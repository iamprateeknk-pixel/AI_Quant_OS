import asyncio
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/strategy_builder_baseline.json"


def test_wrong_declared_rule_is_failed_artifact_replay(tmp_path):
    from ai_quant_os.strategy_builder_evaluation import evaluate_builder_fixture

    fixture = json.loads(FIXTURE.read_bytes())
    fixture["cases"] = fixture["cases"][:1]
    fixture["cases"][0]["expected_rule"] = "FADE_PREVIOUS_CLOSE_DIRECTION"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(fixture))
    report = asyncio.run(evaluate_builder_fixture(changed))
    assert report["matching_cases"] == 0
    assert report["results"][0]["restart_exact"]


def test_declared_builder_cases_retain_actual_review_graph_and_candidate_restart():
    assert importlib.util.find_spec("ai_quant_os.strategy_builder_evaluation") is not None
    from ai_quant_os.strategy_builder_evaluation import evaluate_builder_fixture
    from ai_quant_os.strategy_builder_agent import StrategyCandidate
    from ai_quant_os.strategy_candidate_contracts import CandidateBundle

    report = asyncio.run(evaluate_builder_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 2
    assert not report["proposed_code_executed"] and not report["promotion_approved"]
    for row in report["results"]:
        assert row["restart_exact"] and row["controls_unchanged"]
        Draft202012Validator(StrategyCandidate.model_json_schema()).validate(row["output"])
        Draft202012Validator(CandidateBundle.model_json_schema()).validate(row["candidate"])


def test_retained_builder_report_pins_named_sources_and_current_schemas():
    from ai_quant_os import strategy_builder_evaluation
    from ai_quant_os.hypothesis_review_contracts import HypothesisReview
    from ai_quant_os.strategy_builder_agent import StrategyCandidate
    from ai_quant_os.strategy_candidate_contracts import CandidateBundle

    root = Path(__file__).resolve().parent.parent
    report = json.loads((root / "docs/evaluations/strategy-builder-baseline.json").read_bytes())
    assert report["fixture_sha256"] == sha256(FIXTURE.read_bytes()).hexdigest()
    assert (
        report["evaluator_sha256"]
        == sha256(Path(strategy_builder_evaluation.__file__).read_bytes()).hexdigest()
    )
    for pin in report["source_pins"]:
        assert (
            pin["sha256"]
            == sha256(
                Path(strategy_builder_evaluation.__file__).with_name(pin["path"]).read_bytes()
            ).hexdigest()
        )
    for name, model in [
        ("hypothesis-review", HypothesisReview),
        ("strategy-build-candidate", CandidateBundle),
        ("strategy-candidate", StrategyCandidate),
    ]:
        assert (
            json.loads((root / f"docs/schemas/{name}.schema.json").read_bytes())
            == model.model_json_schema()
        )
    assert report["matching_cases"] == report["case_count"] == 2
