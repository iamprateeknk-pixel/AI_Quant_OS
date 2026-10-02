import importlib
from hashlib import sha256

import pytest

from test_repair_workspace import SOURCE, REPLACEMENT, git, prepare, workspace
from test_repair_workspace import repository as repository


def test_snapshot_reads_complete_committed_package_and_pins_exact_proposed_version(
    tmp_path, repository
):
    value, incident, registry = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    assert hasattr(value, "snapshot"), "Missing candidate source snapshot export"
    snapshot = value.snapshot(candidate["candidate_id"])
    files = {source.path: source for source in snapshot.files}
    assert set(files) == {SOURCE, "src/ai_quant_os/risk.py"}
    assert files[SOURCE].content == REPLACEMENT.encode()
    assert files[SOURCE].sha256 == sha256(REPLACEMENT.encode()).hexdigest()
    assert files["src/ai_quant_os/risk.py"].content == b"LIMIT = 100\n"
    assert snapshot.candidate_commit == candidate["candidate_commit"]
    assert snapshot.rollback_commit == candidate["rollback_commit"]
    assert snapshot.proposed_manifest["agent_version"] == "0.2.1"
    assert snapshot.incident["manifest_ref"] == registry.get("A02", "0.2.0").manifest_ref
    assert snapshot.promotion_allowed is False
    assert len(snapshot.snapshot_sha256) == 64
    assert value.snapshot(candidate["candidate_id"]) == snapshot


def test_snapshot_refuses_untracked_worktree_content_and_non_source_baseline_files(
    tmp_path, repository
):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    assert hasattr(value, "snapshot"), "Missing candidate source snapshot export"
    from pathlib import Path

    extra = Path(candidate["workspace"]) / "untracked.py"
    extra.write_text("untrusted = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="worktree"):
        value.snapshot(candidate["candidate_id"])
    extra.unlink()
    private = repository / "src/ai_quant_os/private.env"
    private.write_text("fixture-only-not-a-secret\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Undeclared package data")
    rejected = prepare(value, incident)
    with pytest.raises(ValueError, match="source"):
        value.snapshot(rejected["candidate_id"])


def test_snapshot_metadata_cannot_be_mutated_through_returned_mappings(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    candidate = prepare(value, incident)
    assert hasattr(value, "snapshot"), "Missing candidate source snapshot export"
    snapshot = value.snapshot(candidate["candidate_id"])
    before = snapshot.snapshot_sha256
    snapshot.proposed_manifest["agent_version"] = "99.0.0"
    snapshot.incident["code"] = "WATCHDOG_FAILURE"
    assert snapshot.proposed_manifest["agent_version"] == "0.2.1"
    assert snapshot.incident["code"] == "AGENT_FAILED"
    assert snapshot.snapshot_sha256 == before


def test_snapshot_identity_changes_when_unchanged_package_dependency_changes(tmp_path, repository):
    value, incident, _ = workspace(tmp_path, repository)
    first = prepare(value, incident)
    assert hasattr(value, "snapshot"), "Missing candidate source snapshot export"
    original = value.snapshot(first["candidate_id"])
    (repository / "src/ai_quant_os/risk.py").write_bytes(b"LIMIT = 101\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "New dependency baseline")
    second = prepare(value, incident)
    changed = value.snapshot(second["candidate_id"])
    assert changed.snapshot_sha256 != original.snapshot_sha256
    assert next(source.sha256 for source in changed.files if source.path == SOURCE) == next(
        source.sha256 for source in original.files if source.path == SOURCE
    )


def test_source_file_snapshot_rejects_alias_paths_and_non_bytes():
    assert importlib.util.find_spec("ai_quant_os.repair_snapshots") is not None, (
        "Missing repair snapshot values"
    )
    from ai_quant_os.repair_snapshots import RepairSourceFile

    for path, content in [("../escape.py", b"x"), (SOURCE.upper(), b"x"), (SOURCE, "not bytes")]:
        with pytest.raises(ValueError):
            RepairSourceFile(path, content)
