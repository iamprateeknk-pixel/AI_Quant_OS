"""CLI artifacts are inert JSON; no challenger implementation is run."""

import json
import os
import subprocess
import sys

from test_agent_genomes import baseline, child, objective


def run(workspace, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.evolution_cli",
            "--workspace",
            str(workspace),
            *map(str, arguments),
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        timeout=15,
    )


def test_seed_register_restart_lineage_and_immutable_export(tmp_path):
    workspace = tmp_path / "registry"
    objective_path = tmp_path / "objective.json"
    objective_path.write_text(objective().model_dump_json(), encoding="utf-8")
    result = run(workspace, "seed", "A02", "--objective", objective_path)
    assert result.returncode == 0, result.stderr.decode()
    root = baseline()
    assert json.loads(result.stdout)["genome_ref"] == root.genome_ref
    candidate = child(root)
    input_path = tmp_path / "candidate.json"
    input_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    result = run(workspace, "register", "--input", input_path)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["state"] == "UNVERIFIED_CHALLENGER"
    assert json.loads(run(workspace, "list").stdout)["genomes"][0]["promotion_allowed"] is False
    lineage = json.loads(run(workspace, "lineage", candidate.genome_ref).stdout)["lineage"]
    assert [row["genome_ref"] for row in lineage] == [root.genome_ref, candidate.genome_ref]
    output = tmp_path / "export.json"
    result = run(workspace, "export", candidate.genome_ref, "--output", output)
    assert result.returncode == 0, result.stderr.decode()
    original = output.read_bytes()
    assert json.loads(original) == candidate.model_dump(mode="json")
    assert run(workspace, "export", candidate.genome_ref, "--output", output).returncode == 0
    assert output.read_bytes() == original
    output.write_bytes(b"keep this file")
    assert run(workspace, "export", candidate.genome_ref, "--output", output).returncode == 2
    assert output.read_bytes() == b"keep this file"


def test_invalid_input_refuses_before_creating_registry(tmp_path):
    workspace = tmp_path / "registry"
    source = tmp_path / "bad.json"
    source.write_bytes(b"{}")
    result = run(workspace, "register", "--input", source)
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Traceback" not in result.stderr
    assert not workspace.exists()


def test_export_refuses_registry_storage_and_non_json_paths(tmp_path):
    workspace = tmp_path / "registry"
    for output in (
        workspace / "export.json",
        tmp_path / "candidate.py",
        tmp_path / "state.sqlite3",
    ):
        result = run(workspace, "export", baseline().genome_ref, "--output", output)
        assert result.returncode == 2
        assert not workspace.exists()


def test_planned_agent_seed_and_oversize_genome_refuse_without_success(tmp_path):
    workspace = tmp_path / "registry"
    metadata = tmp_path / "objective.json"
    metadata.write_text(objective().model_dump_json(), encoding="utf-8")
    result = run(workspace, "seed", "A09", "--objective", metadata)
    assert result.returncode == 2
    assert result.stdout == b""
    oversized = tmp_path / "oversize.json"
    oversized.write_bytes(b" " * 262145)
    result = run(workspace, "register", "--input", oversized)
    assert result.returncode == 2
    assert result.stdout == b""
