"""Source-only test preparation and retained result retrieval are explicit commands."""

import importlib
import json
import subprocess
import sys

from test_strategy_prefix_worker import package as package


def test_cli_prepares_original_package_and_requires_independent_manifest_pin(package, tmp_path):
    assert importlib.util.find_spec("ai_quant_os.candidate_test_cli"), (
        "Candidate tests CLI required"
    )
    root = tmp_path / "cli-tests"
    root.mkdir()
    archive = tmp_path / "paper.validation-packages.sqlite3"
    assert archive.is_file()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.candidate_test_cli",
            "--root",
            str(root),
            "prepare",
            "--validation-archive",
            str(archive),
            "--package-ref",
            package.package_ref,
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()
    value = json.loads(result.stdout)
    assert len(value["manifest_sha256"]) == 64 and value["promotion_allowed"] is False
    refused = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.candidate_test_cli",
            "--root",
            str(root),
            "result",
            "--manifest-sha256",
            "0" * 64,
        ],
        capture_output=True,
        timeout=20,
    )
    assert refused.returncode == 2 and refused.stdout == b""
