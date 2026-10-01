import importlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore


SOURCE = "src/ai_quant_os/regime_agent.py"
ORIGINAL = 'VERSION = "0.2.0"\n'
REPLACEMENT = 'VERSION = "0.2.1"\n'
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def git(repository, *arguments):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    repository = tmp_path / "production"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Repair fixture")
    git(repository, "config", "user.email", "fixture@example.invalid")
    git(repository, "config", "core.autocrlf", "false")
    source = repository / SOURCE
    source.parent.mkdir(parents=True)
    source.write_text(ORIGINAL, encoding="utf-8", newline="\n")
    (source.parent / "risk.py").write_text("LIMIT = 100\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Baseline")
    return repository


def workspace(tmp_path, repository):
    assert importlib.util.find_spec("ai_quant_os.repair_workspace") is not None, (
        "Missing isolated repair candidate workspace"
    )
    module = importlib.import_module("ai_quant_os.repair_workspace")
    registry = load_agent_catalog()
    manifest = registry.get("A02", "0.2.0")
    store = HealthStore(tmp_path / "health.sqlite3")
    incident = store.record(
        HealthIncident(
            "AGENT_FAILED",
            "run-1",
            '{"error":"fixture"}',
            NOW,
            "A02",
            "0.2.0",
            manifest.manifest_ref,
        )
    )
    registry.quarantine("A02", "0.2.0", manifest.manifest_ref)
    return (
        module.RepairWorkspace(repository, tmp_path / "repairs", store, registry),
        incident,
        registry,
    )


def prepare(value, incident, *, replacements=None, version="0.2.1"):
    return value.prepare(
        incident.incident_id,
        new_version=version,
        replacements=replacements if replacements is not None else {SOURCE: REPLACEMENT},
        diagnosis="Observed failure; root cause remains a proposed hypothesis.",
        regression_plan=("Replay the archived incident", "Verify envelope expiry and authority"),
    )


def test_actual_candidate_branch_retains_baseline_and_never_releases_quarantine(
    tmp_path, repository
):
    value, incident, registry = workspace(tmp_path, repository)
    head = git(repository, "rev-parse", "HEAD")
    candidate = prepare(value, incident)
    isolated = Path(candidate["workspace"])
    assert candidate["state"] == "AWAITING_INDEPENDENT_VERIFICATION"
    assert candidate["promotion_allowed"] is False
    assert candidate["blocking_gates"] == [
        "INDEPENDENT_VERIFICATION",
        "SHADOW",
        "CANARY",
        "RELEASE",
    ]
    assert candidate["rollback_commit"] == head
    assert git(repository, "rev-parse", "HEAD") == head
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / SOURCE).read_text(encoding="utf-8") == ORIGINAL
    assert (isolated / SOURCE).read_text(encoding="utf-8") == REPLACEMENT
    assert git(isolated, "branch", "--show-current").startswith("codex/repair-")
    assert git(repository, "show", f"{head}:{SOURCE}") == ORIGINAL.strip()
    assert registry.lifecycle("A02", "0.2.0") == "QUARANTINED"
    with pytest.raises(ValueError, match="Unknown"):
        registry.get("A02", "0.2.1")
    restored, _, _ = workspace(tmp_path, repository)
    assert restored.inspect(candidate["candidate_id"]) == candidate
    assert prepare(restored, incident) == candidate


@pytest.mark.parametrize("path", ["src/ai_quant_os/risk.py", "../escape.py", SOURCE.upper()])
def test_repair_cannot_touch_capital_code_or_escape_allowed_agent_sources(
    tmp_path, repository, path
):
    value, incident, _ = workspace(tmp_path, repository)
    with pytest.raises(ValueError, match="source"):
        prepare(value, incident, replacements={path: REPLACEMENT})
    assert git(repository, "branch", "--list", "codex/repair-*") == ""
    assert git(repository, "status", "--porcelain") == ""


@pytest.mark.parametrize("version", ["0.2.0", "0.1.9", "bad"])
def test_repair_requires_new_immutable_version(tmp_path, repository, version):
    value, incident, _ = workspace(tmp_path, repository)
    with pytest.raises(ValueError, match="version"):
        prepare(value, incident, version=version)
    assert git(repository, "branch", "--list", "codex/repair-*") == ""


def test_non_agent_incident_and_unquarantined_source_cannot_create_repair(tmp_path, repository):
    value, incident, registry = workspace(tmp_path, repository)
    ordinary = value.health.record(HealthIncident("QUOTE_STALE", "EURUSD", "{}", NOW))
    with pytest.raises(ValueError, match="agent"):
        prepare(value, ordinary)
    value.registry = load_agent_catalog()
    with pytest.raises(ValueError, match="quarantine"):
        prepare(value, incident)
    assert registry.lifecycle("A02", "0.2.0") == "QUARANTINED"


def test_dirty_baseline_and_no_change_are_rejected_before_branch_creation(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    with pytest.raises(ValueError, match="change"):
        prepare(value, incident, replacements={SOURCE: ORIGINAL})
    (repository / SOURCE).write_text("dirty = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        prepare(value, incident)
    assert git(repository, "branch", "--list", "codex/repair-*") == ""


def test_modified_candidate_worktree_and_rewritten_branch_are_not_verified_as_original(
    tmp_path, repository
):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    isolated = Path(candidate["workspace"])
    (isolated / SOURCE).write_text("mutated = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="worktree"):
        value.inspect(candidate["candidate_id"])
    git(isolated, "restore", ".")
    git(repository, "update-ref", f"refs/heads/{candidate['branch']}", candidate["rollback_commit"])
    with pytest.raises(ValueError, match="branch"):
        value.inspect(candidate["candidate_id"])


def test_tampered_receipt_is_rejected_instead_of_adopted(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    receipt = tmp_path / "repairs" / "receipts" / f"{candidate['candidate_id']}.json"
    payload = json.loads(receipt.read_bytes())
    payload["rollback_commit"] = "0" * 40
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        value.inspect(candidate["candidate_id"])


def test_crash_after_branch_creation_adopts_only_exact_candidate_on_retry(
    tmp_path, repository, monkeypatch
):
    value, incident, _ = workspace(tmp_path, repository)
    original = value._publish_receipt

    def disk_failure(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(value, "_publish_receipt", disk_failure)
    with pytest.raises(OSError):
        prepare(value, incident)
    branches = git(repository, "branch", "--list", "codex/repair-*", "--format=%(refname:short)")
    assert branches
    monkeypatch.setattr(value, "_publish_receipt", original)
    candidate = prepare(value, incident)
    assert value.inspect(candidate["candidate_id"]) == candidate
    assert (
        git(repository, "branch", "--list", "codex/repair-*", "--format=%(refname:short)")
        == branches
    )


def test_unrecorded_source_mode_change_is_not_adopted_with_rewritten_receipt(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    isolated = Path(candidate["workspace"])
    git(isolated, "update-index", "--chmod=+x", SOURCE)
    tree = git(isolated, "write-tree")
    commit = git(
        isolated, "commit-tree", tree, "-p", candidate["rollback_commit"], "-m", "Tamper mode"
    )
    git(isolated, "reset", "--hard", commit)
    candidate["candidate_commit"] = commit
    receipt = tmp_path / "repairs" / "receipts" / f"{candidate['candidate_id']}.json"
    receipt.write_bytes(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(ValueError, match="source"):
        value.inspect(candidate["candidate_id"])


def test_escaped_dossier_overflow_cannot_publish_unreadable_candidate(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    with pytest.raises(ValueError, match="dossier"):
        value.prepare(
            incident.incident_id,
            new_version="0.2.1",
            replacements={SOURCE: REPLACEMENT},
            diagnosis="Proposed root cause",
            regression_plan=tuple("\U0001f600" * 512 for _ in range(32)),
        )
    assert git(repository, "branch", "--list", "codex/repair-*") == ""
