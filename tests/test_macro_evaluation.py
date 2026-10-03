import asyncio
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/macro_baseline.json"


def test_six_declared_macro_cases_match_actual_graph_and_schema():
    from ai_quant_os.macro_evaluation import evaluate_macro_fixture
    from ai_quant_os.macro_agent import CurrencyMacroBias

    report = asyncio.run(evaluate_macro_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 6
    assert report["promotion_approved"] is False
    for row in report["results"]:
        Draft202012Validator(CurrencyMacroBias.model_json_schema()).validate(row["output"])


def test_declared_metric_mismatch_is_not_rewritten_as_success(tmp_path):
    from ai_quant_os.macro_evaluation import evaluate_macro_fixture

    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_basis_points"]["DFR"] = "-25"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(raw))
    report = asyncio.run(evaluate_macro_fixture(path))
    assert report["matching_cases"] == 5 and report["results"][0]["matches"] is False


def test_cli_preserves_existing_report_and_exits_nonzero_for_mismatch(tmp_path):
    existing = tmp_path / "report.json"
    existing.write_text("retained")
    command = [sys.executable, "-m", "ai_quant_os.macro_evaluation", str(FIXTURE)]
    result = subprocess.run(
        command + ["--output", str(existing)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0 and existing.read_text() == "retained"
    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_basis_points"]["DFR"] = "-25"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    result = subprocess.run(
        [sys.executable, "-m", "ai_quant_os.macro_evaluation", str(changed)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1 and json.loads(result.stdout)["matching_cases"] == 5
