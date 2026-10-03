import asyncio
import importlib.util
import json
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/hypothesis_baseline.json"


def test_declared_hypothesis_cases_use_actual_api_and_exact_restart():
    assert importlib.util.find_spec("ai_quant_os.research_hypothesis_evaluation") is not None
    from ai_quant_os.research_hypothesis_evaluation import evaluate_hypothesis_fixture
    from ai_quant_os.research_hypothesis_contracts import HypothesisSpec

    report = asyncio.run(evaluate_hypothesis_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 5
    assert not report["promotion_approved"] and not report["isolation_verified"]
    for row in report["results"]:
        assert row["restart_exact"] and row["controls_unchanged"]
        assert row["holdout_excluded"] and row["memory_suppressed"]
        Draft202012Validator(HypothesisSpec.model_json_schema()).validate(row["output"])


def test_wrong_expected_hypothesis_rule_is_a_failed_replay(tmp_path):
    assert importlib.util.find_spec("ai_quant_os.research_hypothesis_evaluation") is not None
    from ai_quant_os.research_hypothesis_evaluation import evaluate_hypothesis_fixture

    fixture = json.loads(FIXTURE.read_text())
    fixture["cases"] = fixture["cases"][:1]
    fixture["cases"][0]["expected_rule"] = "FADE_PREVIOUS_CLOSE_DIRECTION"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(fixture))
    report = asyncio.run(evaluate_hypothesis_fixture(changed))
    assert report["matching_cases"] == 0
    assert report["results"][0]["restart_exact"]


def test_retained_hypothesis_report_pins_sources_and_schema():
    from ai_quant_os import research_hypothesis_evaluation
    from ai_quant_os.research_hypothesis_contracts import HypothesisSpec

    root = Path(__file__).resolve().parent.parent
    report = json.loads((root / "docs/evaluations/hypothesis-baseline.json").read_text())
    assert report["fixture_sha256"] == sha256(FIXTURE.read_bytes()).hexdigest()
    assert (
        report["evaluator_sha256"]
        == sha256(Path(research_hypothesis_evaluation.__file__).read_bytes()).hexdigest()
    )
    for pin in report["source_pins"]:
        assert (
            pin["sha256"]
            == sha256(
                Path(research_hypothesis_evaluation.__file__).with_name(pin["path"]).read_bytes()
            ).hexdigest()
        )
    assert (
        json.loads((root / "docs/schemas/hypothesis-spec.schema.json").read_text())
        == HypothesisSpec.model_json_schema()
    )
    assert report["matching_cases"] == report["case_count"] == 5
