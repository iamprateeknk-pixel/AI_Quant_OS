import asyncio
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/event_baseline.json"


def test_seven_declared_event_cases_match_real_graph_and_output_schema():
    from ai_quant_os.event_evaluation import evaluate_event_fixture
    from ai_quant_os.event_agent import EventRiskState

    report = asyncio.run(evaluate_event_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 7
    assert report["promotion_approved"] is False
    for row in report["results"]:
        Draft202012Validator(EventRiskState.model_json_schema()).validate(row["output"])


def test_changed_declared_expectation_is_measured_as_mismatch(tmp_path):
    from ai_quant_os.event_evaluation import evaluate_event_fixture

    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_blackouts"]["EURUSD"] = False
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(raw))
    report = asyncio.run(evaluate_event_fixture(path))
    assert report["matching_cases"] == 6 and report["results"][0]["matches"] is False


def test_cli_preserves_existing_report_and_returns_failure_for_mismatch(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("retained")
    command = [sys.executable, "-m", "ai_quant_os.event_evaluation", str(FIXTURE)]
    result = subprocess.run(
        command + ["--output", str(output)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0 and output.read_text() == "retained"
    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_blackouts"]["EURUSD"] = False
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    result = subprocess.run(
        [sys.executable, "-m", "ai_quant_os.event_evaluation", str(changed)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1 and json.loads(result.stdout)["matching_cases"] == 6
