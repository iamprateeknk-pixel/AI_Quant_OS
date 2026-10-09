import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from test_mt5_sync_checkpoint import implementation as checkpoint_module, source


def implementation():
    module = checkpoint_module()
    assert hasattr(module, "finish_restore")
    return module


def partial(tmp_path, monkeypatch):
    module = checkpoint_module()
    config, health, calls = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    exported = module.export_checkpoint(tmp_path / "source", config, archive)
    destination = tmp_path / "restored"
    link = module.os.link

    def fail(source, target):
        if target.name == "projection.sqlite":
            raise OSError("controlled partial publication")
        link(source, target)

    with monkeypatch.context() as context:
        context.setattr(module.os, "link", fail)
        with pytest.raises(module.CheckpointRejected, match="^STORAGE_UNAVAILABLE$"):
            module.restore_checkpoint(archive, exported.archive_sha256, config, destination)
    marker = destination / "checkpoint-pending.json"
    pin = hashlib.sha256(marker.read_bytes()).hexdigest()
    return module, config, health, calls, archive, exported.archive_sha256, destination, marker, pin


def test_explicit_pinned_finish_preserves_prefix_and_verified_revision(tmp_path, monkeypatch):
    module = implementation()
    _, config, health, calls, archive, archive_pin, target, marker, pin = partial(
        tmp_path, monkeypatch
    )
    original = (target / "gateway.sqlite").read_bytes()
    intent = json.loads(marker.read_bytes())
    assert intent["archive_sha256"] == archive_pin
    result = module.finish_restore(archive, archive_pin, config, target, pin)
    assert result.state == "RESTORE_FINISHED" and result.inspection.health == health
    assert result.publication_sha256 == pin and result.publication_id == UUID(
        intent["publication_id"]
    )
    assert result.may_capture is False and result.native_attestation == "UNVERIFIED"
    assert not marker.exists() and (target / "gateway.sqlite").read_bytes() == original
    assert all(
        (target / name).is_file()
        for name in ("gateway.sqlite", "projection.sqlite", "controller.sqlite")
    )
    assert not (target / "health.json").exists() and len(calls) == 1
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_REQUIRED$"):
        module.finish_restore(archive, archive_pin, config, target, pin)


def test_wrong_publication_pin_writes_no_missing_journal(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, _ = partial(tmp_path, monkeypatch)
    old = marker.read_bytes()
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_MISMATCH$"):
        module.finish_restore(archive, archive_pin, config, target, "0" * 64)
    assert marker.read_bytes() == old and not (target / "projection.sqlite").exists()


def test_changed_partial_journal_is_preserved_and_withheld(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    file = target / "gateway.sqlite"
    changed = file.read_bytes() + b"changed"
    file.write_bytes(changed)
    with pytest.raises(module.CheckpointRejected, match="^PARTIAL_JOURNAL_MISMATCH$"):
        module.finish_restore(archive, archive_pin, config, target, pin)
    assert file.read_bytes() == changed and marker.exists()
    assert not (target / "projection.sqlite").exists()


@pytest.mark.parametrize("name", ["gateway.sqlite-wal", "health.json", "unknown.txt"])
def test_unknown_or_sidecar_files_are_not_adopted_or_removed(tmp_path, monkeypatch, name):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    (target / name).write_bytes(b"retain custody")
    with pytest.raises(module.CheckpointRejected, match="^DESTINATION_LAYOUT$"):
        module.finish_restore(archive, archive_pin, config, target, pin)
    assert (target / name).read_bytes() == b"retain custody" and marker.exists()


def test_legacy_marker_cannot_be_invented_into_a_bound_intent(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, _ = partial(tmp_path, monkeypatch)
    marker.write_bytes(b'{"state":"RESTORING"}\n')
    pin = hashlib.sha256(marker.read_bytes()).hexdigest()
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_INVALID$"):
        module.finish_restore(archive, archive_pin, config, target, pin)
    assert marker.exists() and not (target / "projection.sqlite").exists()


def test_moved_destination_is_not_adopted(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, _, pin = partial(tmp_path, monkeypatch)
    moved = tmp_path / "moved"
    target.rename(moved)
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_MISMATCH$"):
        module.finish_restore(archive, archive_pin, config, moved, pin)
    assert (moved / "checkpoint-pending.json").exists() and not (
        moved / "projection.sqlite"
    ).exists()


def test_owner_contention_blocks_finish_without_any_journal_change(tmp_path, monkeypatch):
    from ai_quant_os.mt5_sync_service import service_ownership

    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    with service_ownership(target / "service-lock.sqlite"):
        with pytest.raises(module.CheckpointRejected, match="^SERVICE_ALREADY_RUNNING$"):
            module.finish_restore(archive, archive_pin, config, target, pin)
    assert marker.exists() and not (target / "projection.sqlite").exists()


def test_second_interruption_retains_intent_and_can_finish_only_explicitly(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    link = module.os.link

    def fail(source, destination):
        if destination.name == "controller.sqlite":
            raise OSError("controlled second interruption")
        link(source, destination)

    with monkeypatch.context() as context:
        context.setattr(module.os, "link", fail)
        with pytest.raises(module.CheckpointRejected, match="^STORAGE_UNAVAILABLE$"):
            module.finish_restore(archive, archive_pin, config, target, pin)
    assert (target / "projection.sqlite").exists() and not (target / "controller.sqlite").exists()
    assert hashlib.sha256(marker.read_bytes()).hexdigest() == pin
    assert (
        module.finish_restore(archive, archive_pin, config, target, pin).state == "RESTORE_FINISHED"
    )


def test_marker_archive_and_policy_bindings_are_required(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_MISMATCH$"):
        module.finish_restore(archive, "0" * 64, config, target, pin)
    changed = config.model_copy(
        update={"schedule": config.schedule.model_copy(update={"interval_seconds": 20})}
    )
    with pytest.raises(module.CheckpointRejected, match="^PUBLICATION_MISMATCH$"):
        module.finish_restore(archive, archive_pin, changed, target, pin)
    assert marker.exists() and not (target / "projection.sqlite").exists()


def test_publication_and_finish_schemas_are_exported(tmp_path):
    from pydantic import TypeAdapter

    module = implementation()
    for name, model in (
        ("demo-checkpoint-publication", module.CheckpointPublication),
        ("demo-checkpoint-finish", module.RestoreCompletion),
    ):
        path = Path("docs/schemas") / (name + ".schema.json")
        assert (
            path.is_file()
            and json.loads(path.read_text(encoding="utf-8")) == TypeAdapter(model).json_schema()
        )


def test_complete_files_with_retained_marker_finish_without_any_republication(
    tmp_path, monkeypatch
):
    module = implementation()
    config, health, _ = source(tmp_path / "source")
    archive = tmp_path / "checkpoint.zip"
    exported = module.export_checkpoint(tmp_path / "source", config, archive)
    target = tmp_path / "restored"
    unlink = Path.unlink

    def fail_marker(path, *args, **kwargs):
        if path.name == "checkpoint-pending.json":
            raise OSError("controlled marker removal failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail_marker)
        with pytest.raises(module.CheckpointRejected, match="^STORAGE_UNAVAILABLE$"):
            module.restore_checkpoint(archive, exported.archive_sha256, config, target)
    marker = target / "checkpoint-pending.json"
    pin = hashlib.sha256(marker.read_bytes()).hexdigest()
    before = {name: (target / name).read_bytes() for name in module.JOURNALS}

    def forbidden(*args, **kwargs):
        raise AssertionError("Existing complete journals must not be linked again")

    monkeypatch.setattr(module.os, "link", forbidden)
    result = module.finish_restore(archive, exported.archive_sha256, config, target, pin)
    assert result.inspection.health == health and not marker.exists()
    assert {name: (target / name).read_bytes() for name in module.JOURNALS} == before


def test_changed_archive_is_withheld_before_missing_journal_publication(tmp_path, monkeypatch):
    module = implementation()
    _, config, _, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    with archive.open("ab") as stream:
        stream.write(b"changed archive")
    with pytest.raises(module.CheckpointRejected, match="^ARCHIVE_MISMATCH$"):
        module.finish_restore(archive, archive_pin, config, target, pin)
    assert marker.exists() and not (target / "projection.sqlite").exists()


def test_real_finish_command_removes_marker_without_native_launch_or_password(
    tmp_path, monkeypatch
):
    import os
    import subprocess
    import sys
    from ai_quant_os.mt5_sync_service import main as service_main
    from test_mt5_sync_service import config_document

    module = implementation()
    _, config, health, _, archive, archive_pin, target, marker, pin = partial(tmp_path, monkeypatch)
    document = config_document()
    document["schedule"] = config.schedule.model_dump(mode="json")
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(document), encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k.upper() != "AI_QUANT_MT5_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").absolute())
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.mt5_sync_checkpoint",
        "--config",
        str(config_file),
        "finish-restore",
        "--archive",
        str(archive),
        "--archive-sha256",
        archive_pin,
        "--destination",
        str(target),
        "--publication-sha256",
        pin,
    ]
    completed = subprocess.run(command, capture_output=True, env=environment, timeout=10)
    assert completed.returncode == 0 and completed.stderr == b""
    result = module.RestoreCompletion.model_validate_json(completed.stdout)
    assert result.inspection.health == health and not marker.exists()
    monkeypatch.delenv("AI_QUANT_MT5_PASSWORD", raising=False)
    assert (
        service_main(["--config", str(config_file), "--state-dir", str(target), "--inspect-state"])
        == 0
    )
