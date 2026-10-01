import importlib
import json
import subprocess
import sys

import pytest

from test_repair_workspace import SOURCE, git, workspace
from test_repair_workspace import repository as repository


def cli(repository, tmp_path, *arguments):
    assert importlib.util.find_spec("ai_quant_os.repair_cli") is not None, "Missing repair CLI"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.repair_cli",
            "--repository",
            str(repository),
            "--root",
            str(tmp_path / "repairs"),
            "--health-store",
            str(tmp_path / "health.sqlite3"),
            *arguments,
        ],
        capture_output=True,
        text=True,
    )


def plan(tmp_path, incident, **overrides):
    path = tmp_path / "plan.json"
    payload = {
        "schema_version": "1.0",
        "incident_id": incident.incident_id,
        "new_version": "0.2.1",
        "replacements": {SOURCE: 'raise RuntimeError("MUST_NOT_EXECUTE_CANDIDATE")\n'},
        "diagnosis": "An unverified repair hypothesis",
        "regression_plan": ["Replay the incident"],
        **overrides,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_proposes_and_reads_pinned_candidate_without_executing_it(tmp_path, repository):
    _, incident, _ = workspace(tmp_path, repository)
    head = git(repository, "rev-parse", "HEAD")
    created = cli(repository, tmp_path, "prepare", str(plan(tmp_path, incident)))
    assert created.returncode == 0, created.stderr
    receipt = json.loads(created.stdout)
    assert receipt["state"] == "AWAITING_INDEPENDENT_VERIFICATION"
    assert receipt["promotion_allowed"] is False
    assert "MUST_NOT_EXECUTE_CANDIDATE" not in created.stdout
    read = cli(repository, tmp_path, "inspect", receipt["candidate_id"])
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout) == receipt
    assert git(repository, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("overrides", [{"promotion_allowed": True}, {"schema_version": "2.0"}])
def test_cli_rejects_client_authority_and_unrecognized_contract(tmp_path, repository, overrides):
    _, incident, _ = workspace(tmp_path, repository)
    result = cli(repository, tmp_path, "prepare", str(plan(tmp_path, incident, **overrides)))
    assert result.returncode == 2
    assert not result.stdout
    assert git(repository, "branch", "--list", "codex/repair-*") == ""


def test_cli_missing_health_journal_does_not_create_fake_incident_source(tmp_path, repository):
    journal = tmp_path / "health.sqlite3"
    result = cli(repository, tmp_path, "inspect", "a" * 64)
    assert result.returncode == 2
    assert not journal.exists()


def test_sandbox_status_does_not_open_health_journal_or_accept_missing_image(tmp_path, repository):
    result = cli(
        repository,
        tmp_path,
        "sandbox-status",
        "--image-id",
        "sha256:" + "0" * 64,
        "--suite-sha256",
        "1" * 64,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["available"] is False
    assert payload["promotion_allowed"] is False
    assert payload["container_admission_required"] is True
    assert payload["suite_verification_required"] is True
    assert not (tmp_path / "health.sqlite3").exists()
