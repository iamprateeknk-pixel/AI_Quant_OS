import importlib
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from test_repair_workspace import SOURCE, git, prepare, workspace
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


@pytest.mark.parametrize(
    ("oom_killed", "expected_return", "expected_state"),
    [
        (False, 0, "EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION"),
        (True, 1, "EXECUTION_FAILED_AWAITING_REVIEW"),
    ],
)
def test_execute_cli_runs_only_through_owned_container_and_reports_unattested_result(
    tmp_path, repository, monkeypatch, capsys, oom_killed, expected_return, expected_state
):
    from ai_quant_os.repair_cli import main
    from ai_quant_os.repair_transport import CommandResult
    from test_repair_containers import DockerFixture, IMAGE

    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    suite = b"print('cli-verifier-result')\n"
    suite_file = tmp_path / "suite.py"
    suite_file.write_bytes(suite)
    suite_sha = sha256(suite).hexdigest()
    fixture = DockerFixture(monkeypatch, type("Job", (), {"path": tmp_path})())
    fixture.oom_killed = oom_killed
    starts = []

    def attached(executable, *arguments, timeout):
        starts.append((arguments, timeout))
        fixture.started = True
        return CommandResult(0, b"cli-verifier-result\n", b"")

    monkeypatch.setattr("ai_quant_os.repair_containers.local_docker_command", attached)
    result = main(
        [
            "--repository",
            str(repository),
            "--root",
            str(tmp_path / "repairs"),
            "--health-store",
            str(tmp_path / "health.sqlite3"),
            "execute",
            candidate["candidate_id"],
            "--suite-file",
            str(suite_file),
            "--suite-sha256",
            suite_sha,
            "--image-id",
            IMAGE,
        ]
    )

    assert result == expected_return
    output = capsys.readouterr().out
    receipt = json.loads(output)
    from jsonschema import validate

    validate(
        receipt,
        json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/schemas/repair-execution-receipt.schema.json"
            ).read_bytes()
        ),
    )
    assert receipt["schema_version"] == "1.0"
    assert receipt["candidate_id"] == candidate["candidate_id"]
    assert receipt["state"] == expected_state
    assert receipt["execution_verified"] is False
    assert receipt["promotion_allowed"] is False
    assert receipt["oom_killed"] is oom_killed
    assert Path(receipt["evidence_root"]) == tmp_path / "repairs" / "jobs"
    assert starts == [(("container", "start", "--attach", "c" * 64), 45)]
    assert fixture.calls[-1] == ("container", "rm", "--force", "--volumes", "c" * 64)
    assert (tmp_path / "repairs" / "jobs" / receipt["stdout_artifact"]).read_bytes() == (
        b"cli-verifier-result\n"
    )
    assert "cli-verifier-result" not in output


def test_execution_status_cli_reads_receipt_without_health_store_or_docker(
    tmp_path, repository, monkeypatch, capsys
):
    from ai_quant_os.repair_containers import RepairExecutionRecovery
    from ai_quant_os.repair_cli import main

    calls = []

    def read(root, owner):
        calls.append((root, owner))
        return RepairExecutionRecovery(
            schema_version="1.0",
            owner=owner,
            state="EXECUTION_OUTCOME_UNCONFIRMED",
            retained_artifacts=("start-intent.json",),
            receipt=None,
        )

    monkeypatch.setattr("ai_quant_os.repair_cli.read_repair_execution", read)
    owner = "a" * 32
    result = main(
        [
            "--repository",
            str(repository),
            "--root",
            str(tmp_path / "repairs"),
            "execution-status",
            owner,
        ]
    )

    assert result == 0
    assert calls == [(tmp_path / "repairs" / "jobs", owner)]
    assert json.loads(capsys.readouterr().out)["state"] == "EXECUTION_OUTCOME_UNCONFIRMED"


def test_snapshot_cli_returns_inventory_and_version_without_source_payload(tmp_path, repository):
    _, incident, _ = workspace(tmp_path, repository)
    created = cli(repository, tmp_path, "prepare", str(plan(tmp_path, incident)))
    assert created.returncode == 0, created.stderr
    candidate = json.loads(created.stdout)
    result = cli(repository, tmp_path, "snapshot", candidate["candidate_id"])
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["schema_version"] == "1.0"
    assert summary["candidate_commit"] == candidate["candidate_commit"]
    assert summary["proposed_version"] == "0.2.1"
    assert summary["state"] == "SNAPSHOT_ONLY"
    assert summary["promotion_allowed"] is False
    assert len(summary["inventory"]) == 2
    assert "MUST_NOT_EXECUTE_CANDIDATE" not in result.stdout
