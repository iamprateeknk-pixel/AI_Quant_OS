import importlib.util
import json
import subprocess
import sys

from test_research_evidence_catalog import raw_bars


def test_actual_cli_registers_and_reopens_catalog_and_refuses_label_conflict(tmp_path):
    assert importlib.util.find_spec("ai_quant_os.research_data_cli") is not None
    path = tmp_path / "source.csv"
    path.write_bytes(raw_bars())
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.research_data_cli",
        "--catalog",
        str(tmp_path / "catalog.sqlite3"),
    ]
    register = command + [
        "register",
        str(path),
        "--source-id",
        "declared-history",
        "--resource-class",
        "TRAINING",
    ]
    result = subprocess.run(register, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["row_count"] == 3 and not receipt["execution_authorized"]
    result = subprocess.run(command + ["list"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout) == [receipt]
    result = subprocess.run(register[:-1] + ["HOLDOUT"], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "invalid choice" in result.stderr and not result.stdout
    result = subprocess.run(command + ["list"], capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout) == [receipt]
