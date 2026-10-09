import hashlib
import importlib.util
import json
import sqlite3
import zipfile
from contextlib import nullcontext

import pytest

from test_mt5_sync_controller import setup


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_sync_checkpoint") is not None
    from ai_quant_os import mt5_sync_checkpoint

    return mt5_sync_checkpoint


def source(path, *, capture=True):
    from ai_quant_os.mt5_sync_service import ServiceConfig

    path.mkdir()
    _, controller, gateway, projection, _, _, calls, policy = setup(path)
    if capture:
        controller.tick()
    health = controller.inspect_health()
    config = ServiceConfig(connection=controller.connection, schedule=policy)
    controller.close()
    gateway.close()
    projection.close()
    return config, health, calls


def test_export_and_restore_preserve_exact_revision_deals_and_original_state(tmp_path):
    module = implementation()
    config, health, calls = source(tmp_path / "source")
    marker = tmp_path / "source/health.json"
    marker.write_bytes(b"prior heartbeat")
    archive = tmp_path / "checkpoint.zip"
    result = module.export_checkpoint(tmp_path / "source", config, archive)
    assert result.state == "EXPORTED" and result.inspection.health == health
    pin = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result.archive_sha256 == pin and len(calls) == 1
    with zipfile.ZipFile(archive) as entries:
        assert set(entries.namelist()) == {
            "manifest.json",
            "gateway.sqlite",
            "projection.sqlite",
            "controller.sqlite",
        }
        manifest = json.loads(entries.read("manifest.json"))
        assert manifest["inspection"]["projection"]["known_deals"] == 2
        assert "password" not in entries.read("manifest.json").decode()
    restored = module.restore_checkpoint(archive, pin, config, tmp_path / "restored")
    assert restored.state == "RESTORED" and restored.inspection.health == health
    assert restored.inspection.projection.known_deals == 2
    assert restored.inspection.gateway_custody == "ALL_FINISHED"
    assert restored.may_capture is False and restored.may_affect_portfolio is False
    assert marker.read_bytes() == b"prior heartbeat"
    assert not (tmp_path / "restored/health.json").exists()


def test_wrong_archive_pin_refuses_before_destination_creation(tmp_path):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    module.export_checkpoint(tmp_path / "source", config, archive)
    with pytest.raises(module.CheckpointRejected, match="^ARCHIVE_MISMATCH$"):
        module.restore_checkpoint(archive, "0" * 64, config, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_refuses_changed_policy_without_publishing_destination(tmp_path):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    result = module.export_checkpoint(tmp_path / "source", config, archive)
    changed = config.model_copy(
        update={"schedule": config.schedule.model_copy(update={"interval_seconds": 20})}
    )
    with pytest.raises(module.CheckpointRejected, match="^POLICY_MISMATCH$"):
        module.restore_checkpoint(archive, result.archive_sha256, changed, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_existing_archive_and_destination_are_never_overwritten(tmp_path):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    result = module.export_checkpoint(tmp_path / "source", config, archive)
    original = archive.read_bytes()
    with pytest.raises(module.CheckpointRejected, match="^DESTINATION_EXISTS$"):
        module.export_checkpoint(tmp_path / "source", config, archive)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep").write_bytes(b"existing")
    with pytest.raises(module.CheckpointRejected, match="^DESTINATION_EXISTS$"):
        module.restore_checkpoint(archive, result.archive_sha256, config, target)
    assert archive.read_bytes() == original and (target / "keep").read_bytes() == b"existing"


def test_service_refuses_partial_checkpoint_before_creating_missing_journals(tmp_path, capsys):
    from ai_quant_os import mt5_sync_service
    from test_mt5_sync_service import config_document

    values = config_document(enabled=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(values), encoding="utf-8")
    target = tmp_path / "partial"
    target.mkdir()
    (target / "checkpoint-pending.json").write_text('{"state":"RESTORING"}', encoding="utf-8")
    assert mt5_sync_service.main(["--config", str(config), "--state-dir", str(target)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CHECKPOINT_INCOMPLETE"
    assert not (target / "gateway.sqlite").exists()


def test_active_service_owner_blocks_export(tmp_path):
    from ai_quant_os.mt5_sync_service import service_ownership

    module = implementation()
    config, _, _ = source(tmp_path / "source")
    with service_ownership(tmp_path / "source/service-lock.sqlite"):
        with pytest.raises(module.CheckpointRejected, match="^SERVICE_ALREADY_RUNNING$"):
            module.export_checkpoint(tmp_path / "source", config, tmp_path / "checkpoint.zip")
    assert not (tmp_path / "checkpoint.zip").exists()


def test_corrupt_audit_cannot_be_exported_or_reset(tmp_path):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    with sqlite3.connect(tmp_path / "source/controller.sqlite") as db:
        db.execute("DELETE FROM audit_events")
    with pytest.raises(module.CheckpointRejected, match="^JOURNAL_UNAVAILABLE$"):
        module.export_checkpoint(tmp_path / "source", config, tmp_path / "checkpoint.zip")
    assert not (tmp_path / "checkpoint.zip").exists()
    with sqlite3.connect(tmp_path / "source/controller.sqlite") as db:
        assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0


@pytest.mark.parametrize("member", ["../escape", "health.json", "manifest.json"])
def test_extra_or_duplicate_archive_members_are_refused_even_with_new_pin(tmp_path, member):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    module.export_checkpoint(tmp_path / "source", config, archive)
    with zipfile.ZipFile(archive, "a") as entries:
        with pytest.warns(UserWarning) if member == "manifest.json" else nullcontext():
            entries.writestr(member, b"unexpected")
    pin = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(module.CheckpointRejected, match="^ARCHIVE_INVALID$"):
        module.restore_checkpoint(archive, pin, config, tmp_path / "restored")
    assert not (tmp_path / "restored").exists() and not (tmp_path / "escape").exists()


def test_missing_journals_do_not_create_source_or_archive(tmp_path):
    module = implementation()
    config, _, _ = source(tmp_path / "fixture")
    with pytest.raises(module.CheckpointRejected, match="^STATE_REQUIRED$"):
        module.export_checkpoint(tmp_path / "missing", config, tmp_path / "checkpoint.zip")
    assert not (tmp_path / "missing").exists() and not (tmp_path / "checkpoint.zip").exists()


def test_uncertain_worker_and_pending_identity_survive_restore_without_adoption(tmp_path):
    from ai_quant_os.mt5_demo_worker import SupervisedSync
    from ai_quant_os.mt5_sync_service import ServiceConfig

    module = implementation()
    original = tmp_path / "source"
    original.mkdir()
    _, controller, gateway, projection, _, _, calls, policy = setup(original)
    gateway.runner = lambda request, *, clock, worker_id: SupervisedSync(
        state="WITHHELD", code="TIMEOUT", worker_id=worker_id, worker_custody="UNKNOWN"
    )
    halted = controller.tick()
    config = ServiceConfig(connection=controller.connection, schedule=policy)
    controller.close()
    gateway.close()
    projection.close()
    archive = tmp_path / "checkpoint.zip"
    exported = module.export_checkpoint(original, config, archive)
    restored = module.restore_checkpoint(
        archive, exported.archive_sha256, config, tmp_path / "restored"
    )
    assert restored.inspection.gateway_custody == "CUSTODY_UNCERTAIN"
    assert restored.inspection.health == halted and halted.pending_attempt_id is not None
    assert restored.inspection.pending_receipt.state == "UNAVAILABLE" and calls == []
    with sqlite3.connect(original / "gateway.sqlite") as db:
        old = db.execute("SELECT row_json FROM demo_gateway_attempts").fetchall()
    with sqlite3.connect(tmp_path / "restored/gateway.sqlite") as db:
        assert db.execute("SELECT row_json FROM demo_gateway_attempts").fetchall() == old


@pytest.mark.parametrize("change", ["journal", "inspection", "duplicate_policy"])
def test_pinned_archive_still_requires_internal_commitments_and_audited_state(tmp_path, change):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    module.export_checkpoint(tmp_path / "source", config, archive)
    with zipfile.ZipFile(archive) as entries:
        files = {name: entries.read(name) for name in entries.namelist()}
    if change == "journal":
        files["controller.sqlite"] = files["controller.sqlite"][:-1] + b"X"
    elif change == "inspection":
        manifest = json.loads(files["manifest.json"])
        manifest["inspection"]["health"]["revision"] += 1
        files["manifest.json"] = json.dumps(manifest).encode()
    else:
        files["manifest.json"] = files["manifest.json"].replace(
            b"{", b'{"policy_ref":"' + b"0" * 64 + b'",', 1
        )
    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(forged, "x") as output:
        for name, data in files.items():
            output.writestr(name, data)
    pin = hashlib.sha256(forged.read_bytes()).hexdigest()
    with pytest.raises(module.CheckpointRejected, match="^ARCHIVE_INVALID$"):
        module.restore_checkpoint(forged, pin, config, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_partial_publication_is_retained_and_never_automatically_retried(tmp_path, monkeypatch):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    result = module.export_checkpoint(tmp_path / "source", config, archive)
    link = module.os.link

    def fail_second_journal(source, target):
        if target.name == "projection.sqlite":
            raise OSError("controlled disk publication failure")
        link(source, target)

    monkeypatch.setattr(module.os, "link", fail_second_journal)
    target = tmp_path / "restored"
    with pytest.raises(module.CheckpointRejected, match="^STORAGE_UNAVAILABLE$"):
        module.restore_checkpoint(archive, result.archive_sha256, config, target)
    assert (target / "gateway.sqlite").is_file() and not (target / "projection.sqlite").exists()
    assert (target / "checkpoint-pending.json").is_file()
    with pytest.raises(module.CheckpointRejected, match="^DESTINATION_EXISTS$"):
        module.restore_checkpoint(archive, result.archive_sha256, config, target)


def test_archive_size_limit_is_checked_before_restore_publication(tmp_path, monkeypatch):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    result = module.export_checkpoint(tmp_path / "source", config, archive)
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", archive.stat().st_size - 1)
    with pytest.raises(module.CheckpointRejected, match="^CAPACITY$"):
        module.restore_checkpoint(archive, result.archive_sha256, config, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_all_journal_writers_are_excluded_during_snapshot(tmp_path, monkeypatch):
    module = implementation()
    config, _, _ = source(tmp_path / "source")
    snapshot = module._snapshot
    attempted = []

    def check_writers(source, target):
        for name in ("gateway.sqlite", "projection.sqlite", "controller.sqlite"):
            with sqlite3.connect(source.parent / name, timeout=0) as competing:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competing.execute("BEGIN IMMEDIATE")
            attempted.append(name)
        return snapshot(source, target)

    monkeypatch.setattr(module, "_snapshot", check_writers)
    module.export_checkpoint(tmp_path / "source", config, tmp_path / "checkpoint.zip")
    assert len(attempted) == 9


def test_checkpoint_schema_is_exported_and_rejects_capture_authority(tmp_path):
    from pathlib import Path
    from pydantic import TypeAdapter, ValidationError

    module = implementation()
    schema = Path("docs/schemas/demo-sync-checkpoint.schema.json")
    assert schema.is_file()
    assert (
        json.loads(schema.read_text(encoding="utf-8"))
        == TypeAdapter(module.SyncCheckpoint).json_schema()
    )
    config, _, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    module.export_checkpoint(tmp_path / "source", config, archive)
    with zipfile.ZipFile(archive) as entries:
        manifest = json.loads(entries.read("manifest.json"))
    manifest["may_capture"] = True
    with pytest.raises(ValidationError):
        module.SyncCheckpoint.model_validate(manifest)


def test_real_checkpoint_cli_exports_and_restores_without_password_or_native_work(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from test_mt5_sync_service import config_document

    module = implementation()
    config, health, _ = source(tmp_path / "source", capture=False)
    values = config_document()
    values["schedule"] = config.schedule.model_dump(mode="json")
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(values), encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k.upper() != "AI_QUANT_MT5_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").absolute())
    prefix = [sys.executable, "-m", "ai_quant_os.mt5_sync_checkpoint", "--config", str(config_file)]
    archive = tmp_path / "checkpoint.zip"
    exported = subprocess.run(
        prefix + ["export", "--state-dir", str(tmp_path / "source"), "--archive", str(archive)],
        capture_output=True,
        env=environment,
        timeout=15,
    )
    assert exported.returncode == 0 and exported.stderr == b""
    pin = json.loads(exported.stdout)["archive_sha256"]
    restored = subprocess.run(
        prefix
        + [
            "restore",
            "--archive",
            str(archive),
            "--archive-sha256",
            pin,
            "--destination",
            str(tmp_path / "restored"),
        ],
        capture_output=True,
        env=environment,
        timeout=15,
    )
    assert restored.returncode == 0 and restored.stderr == b""
    result = module.CheckpointResult.model_validate_json(restored.stdout)
    assert result.state == "RESTORED" and result.inspection.health == health
    assert not (tmp_path / "restored/health.json").exists()
