import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/supervisor_baseline.json"


def test_six_declared_cases_use_actual_api_graph_and_restart_receipts():
    assert importlib.util.find_spec("ai_quant_os.supervisor_evaluation") is not None
    from ai_quant_os.supervisor_evaluation import evaluate_supervisor_fixture
    from ai_quant_os.supervisor_agent import TaskPlan

    report = asyncio.run(evaluate_supervisor_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 6
    assert not report["promotion_approved"]
    for row in report["results"]:
        assert row["restart_exact"]
        Draft202012Validator(TaskPlan.model_json_schema()).validate(row["output"])


def test_metric_mismatch_is_reported_and_cli_preserves_retained_report(tmp_path):
    assert importlib.util.find_spec("ai_quant_os.supervisor_evaluation") is not None
    from ai_quant_os.supervisor_evaluation import evaluate_supervisor_fixture

    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_statuses"]["A02"] = "COMPLETED"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    report = asyncio.run(evaluate_supervisor_fixture(changed))
    assert report["matching_cases"] == 5 and not report["results"][0]["matches"]
    retained = tmp_path / "retained.json"
    retained.write_text("retained")
    command = [sys.executable, "-m", "ai_quant_os.supervisor_evaluation", str(changed)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and json.loads(result.stdout)["matching_cases"] == 5
    result = subprocess.run(command + ["--output", str(retained)], capture_output=True, timeout=60)
    assert result.returncode != 0 and retained.read_text() == "retained"
