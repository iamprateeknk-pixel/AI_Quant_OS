"""Local launch refusals are sanitized and leave existing services untouched."""

import os
import subprocess
import sys


def command(*args):
    return subprocess.run(
        [sys.executable, "-m", "ai_quant_os.local_cli", *map(str, args)],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        timeout=10,
    )


def test_invalid_duration_refuses_before_starting_or_creating_state(tmp_path):
    state = tmp_path / "state"
    result = command("--state", state, "--run-seconds", "-1")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Traceback" not in result.stderr
    assert not state.exists()


def test_missing_checkout_refuses_without_printing_operator_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_QUANT_OPERATOR_TOKEN", "fixture-never-print-token")
    result = command("--checkout", tmp_path, "--state", tmp_path / "state", "--run-seconds", ".1")
    assert result.returncode == 2
    assert b"fixture-never-print-token" not in result.stdout + result.stderr
    assert b"Traceback" not in result.stderr
    assert not (tmp_path / "state").exists()
