import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures/liquidity_baseline.json"


def test_actual_persisted_liquidity_graph_matches_declared_baseline_and_schema():
    from ai_quant_os.liquidity_evaluation import evaluate_liquidity_fixture
    from ai_quant_os.liquidity_agent import LiquidityState

    report = asyncio.run(evaluate_liquidity_fixture(FIXTURE))
    assert report["matching_cases"] == report["case_count"] == 6
    assert report["promotion_approved"] is False
    for case in report["results"]:
        Draft202012Validator(LiquidityState.model_json_schema()).validate(case["output"])


def test_baseline_mismatch_is_reported_and_invalid_recipe_is_rejected(tmp_path):
    from ai_quant_os.liquidity_evaluation import evaluate_liquidity_fixture

    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_imbalance"] = "0.8"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(raw))
    report = asyncio.run(evaluate_liquidity_fixture(path))
    assert report["matching_cases"] == 5
    assert report["results"][0]["matches"] is False
    raw["cases"][0]["prices"] = ["1"] * 65
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        asyncio.run(evaluate_liquidity_fixture(path))


def test_cli_refuses_overwrite_and_reports_mismatch_with_nonzero_exit(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("retained report")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.liquidity_evaluation",
            str(FIXTURE),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0 and output.read_text() == "retained report"
    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["expected_imbalance"] = "0.8"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    result = subprocess.run(
        [sys.executable, "-m", "ai_quant_os.liquidity_evaluation", str(changed)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["matching_cases"] == 5
