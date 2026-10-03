import asyncio
import importlib.util
import json
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/incident_baseline.json"


def test_declared_incident_cases_replay_actual_api_and_restart_archives():
    assert importlib.util.find_spec("ai_quant_os.incident_evaluation") is not None
    from ai_quant_os.incident_evaluation import evaluate_incident_fixture
    from ai_quant_os.incident_agent import IncidentRecommendation, WorkflowProposal

    report = asyncio.run(evaluate_incident_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 4
    assert not report["promotion_approved"]
    for row in report["results"]:
        assert row["restart_exact"] and row["controls_unchanged"]
        Draft202012Validator(IncidentRecommendation.model_json_schema()).validate(row["output"])
        Draft202012Validator(WorkflowProposal.model_json_schema()).validate(row["proposal"])


def test_changed_expected_safety_state_is_a_failed_evaluation(tmp_path):
    assert importlib.util.find_spec("ai_quant_os.incident_evaluation") is not None
    from ai_quant_os.incident_evaluation import evaluate_incident_fixture

    raw = json.loads(FIXTURE.read_text())
    raw["cases"] = raw["cases"][:1]
    raw["cases"][0]["expected_state"] = "NORMAL"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    report = asyncio.run(evaluate_incident_fixture(changed))
    assert report["matching_cases"] == 0
    assert report["results"][0]["controls_unchanged"]


def test_retained_incident_report_pins_actual_sources_and_current_schemas():
    from ai_quant_os import incident_evaluation
    from ai_quant_os.incident_agent import IncidentRecommendation, WorkflowProposal

    root = Path(__file__).resolve().parent.parent
    report = json.loads((root / "docs/evaluations/incident-baseline.json").read_text())
    assert report["fixture_sha256"] == sha256(FIXTURE.read_bytes()).hexdigest()
    assert (
        report["evaluator_sha256"]
        == sha256(Path(incident_evaluation.__file__).read_bytes()).hexdigest()
    )
    for name, digest in report["source_sha256"].items():
        assert (
            digest
            == sha256(Path(incident_evaluation.__file__).with_name(name).read_bytes()).hexdigest()
        )
    for name, model in [
        ("incident-recommendation", IncidentRecommendation),
        ("incident-workflow-proposal", WorkflowProposal),
    ]:
        assert (
            json.loads((root / f"docs/schemas/{name}.schema.json").read_text())
            == model.model_json_schema()
        )
    assert report["matching_cases"] == report["case_count"] == 4
