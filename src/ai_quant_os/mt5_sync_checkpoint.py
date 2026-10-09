"""Offline, independently pinned copies of retained demo sync journals."""

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import UUID4, Field, model_validator

from ai_quant_os.mt5_demo_gateway import DemoObservationGateway, canonical, request_identity
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_sync_controller import DemoSyncController
from ai_quant_os.mt5_sync_inspection import SyncInspection, inspect_sync
from ai_quant_os.mt5_sync_projection import SyncProjectionStore
from ai_quant_os.mt5_sync_service import (
    CHECKPOINT_MARKER,
    PASSWORD_ENV,
    ServiceConfig,
    ServiceRejected,
    _unique,
    load_config,
    service_ownership,
)

JOURNALS = ("gateway.sqlite", "projection.sqlite", "controller.sqlite")
MAX_JOURNAL_BYTES = 96 * 1024 * 1024
MAX_ARCHIVE_BYTES = 3 * MAX_JOURNAL_BYTES + 65536
MAX_MANIFEST_BYTES = 32768
BLOCK = 65536


class CheckpointRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class JournalCommitment(ObservationModel):
    name: Literal["gateway.sqlite", "projection.sqlite", "controller.sqlite"]
    bytes: int = Field(gt=0, le=MAX_JOURNAL_BYTES, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SyncCheckpoint(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    artifact_kind: Literal["LOCAL_UNVERIFIED_SYNC_CHECKPOINT"] = "LOCAL_UNVERIFIED_SYNC_CHECKPOINT"
    policy_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    journals: tuple[JournalCommitment, ...] = Field(min_length=3, max_length=3)
    inspection: SyncInspection
    may_capture: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def complete_inventory(self):
        if tuple(j.name for j in self.journals) != JOURNALS or self.inspection.state != "READABLE":
            raise ValueError("Invalid checkpoint inventory")
        return self


class CheckpointResult(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["EXPORTED", "RESTORED"]
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    inspection: SyncInspection
    may_capture: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


class CheckpointPublication(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["RESTORING"] = "RESTORING"
    publication_id: UUID4
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    journals: tuple[JournalCommitment, ...] = Field(min_length=3, max_length=3)
    may_capture: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def complete_inventory(self):
        if tuple(j.name for j in self.journals) != JOURNALS:
            raise ValueError("Invalid publication inventory")
        return self


class RestoreCompletion(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["RESTORE_FINISHED"] = "RESTORE_FINISHED"
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication_id: UUID4
    inspection: SyncInspection
    may_capture: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"


def _policy(config):
    return hashlib.sha256(
        canonical(
            [request_identity(config.connection), config.schedule.model_dump(mode="json")]
        ).encode()
    ).hexdigest()


def _digest(stream, limit):
    stream.seek(0)
    digest, count = hashlib.sha256(), 0
    while block := stream.read(BLOCK):
        count += len(block)
        if count > limit:
            raise CheckpointRejected("CAPACITY")
        digest.update(block)
    return digest.hexdigest(), count


def _commitments(path):
    inventory = []
    for name in JOURNALS:
        with (path / name).open("rb") as stream:
            digest, count = _digest(stream, MAX_JOURNAL_BYTES)
        inventory.append(JournalCommitment(name=name, bytes=count, sha256=digest))
    return tuple(inventory)


def _destination_ref(path):
    identity = path.stat()
    return hashlib.sha256(
        canonical(
            [os.path.normcase(str(path.resolve())), identity.st_dev, identity.st_ino]
        ).encode()
    ).hexdigest()


@contextmanager
def _staging(parent):
    path = Path(tempfile.mkdtemp(prefix="demo-checkpoint-", dir=parent))
    try:
        yield path
    finally:
        # Only fixed files created in this exclusively owned directory are removed.
        for name in (*JOURNALS, "manifest.json", "archive.zip"):
            for suffix in ("", "-wal", "-shm", "-journal"):
                (path / (name + suffix)).unlink(missing_ok=True)
        path.rmdir()


def _snapshot(source, target):
    deadline = time.monotonic() + 20

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise CheckpointRejected("SNAPSHOT_TIMEOUT")

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0)) as original:
        pages = original.execute("PRAGMA page_count").fetchone()[0]
        size = original.execute("PRAGMA page_size").fetchone()[0]
        if pages * size > MAX_JOURNAL_BYTES:
            raise CheckpointRejected("CAPACITY")
        with closing(sqlite3.connect(target)) as copy:
            original.backup(copy, pages=128, progress=progress, sleep=0.01)


def _inspect(path, config):
    with ExitStack() as stack:
        gateway = DemoObservationGateway(path / JOURNALS[0])
        stack.callback(gateway.close)
        projection = SyncProjectionStore(path / JOURNALS[1])
        stack.callback(projection.close)
        controller = DemoSyncController(
            path / JOURNALS[2], config.connection, config.schedule, gateway, projection
        )
        stack.callback(controller.close)
        inspection = inspect_sync(controller)
        if inspection.state != "READABLE":
            raise CheckpointRejected("JOURNAL_UNAVAILABLE")
        return inspection


def _stable(inspection):
    # Receipt facts/quote freshness expire; durable commitments and revisions do not.
    data = inspection.model_dump(mode="json")
    data.pop("inspected_at")
    data.pop("clock_condition")
    data["projection"].pop("quote_context")
    for field in ("report_state", "report_code", "gateway_code"):
        data["pending_receipt"].pop(field)
    return data


def export_checkpoint(state_dir: Path, config: ServiceConfig, archive: Path) -> CheckpointResult:
    state_dir, archive = state_dir.absolute(), archive.absolute()
    if archive.exists():
        raise CheckpointRejected("DESTINATION_EXISTS")
    if not all((state_dir / name).is_file() for name in JOURNALS):
        raise CheckpointRejected("STATE_REQUIRED")
    try:
        with (
            service_ownership(state_dir / "service-lock.sqlite"),
            _staging(archive.parent) as stage,
        ):
            if (state_dir / CHECKPOINT_MARKER).exists():
                raise CheckpointRejected("CHECKPOINT_INCOMPLETE")
            before = _inspect(state_dir, config)
            with ExitStack() as locks:
                for name in JOURNALS:
                    connection = locks.enter_context(
                        closing(
                            sqlite3.connect(
                                (state_dir / name).as_uri() + "?mode=rw", uri=True, timeout=0
                            )
                        )
                    )
                    connection.execute("BEGIN IMMEDIATE")
                for name in JOURNALS:
                    _snapshot(state_dir / name, stage / name)
                inspection = _inspect(stage, config)
            if _stable(before) != _stable(inspection) or _stable(before) != _stable(
                _inspect(state_dir, config)
            ):
                raise CheckpointRejected("STATE_CHANGED")
            manifest = SyncCheckpoint(
                policy_ref=_policy(config), journals=_commitments(stage), inspection=inspection
            )
            raw = manifest.model_dump_json().encode("utf-8")
            if len(raw) > MAX_MANIFEST_BYTES:
                raise CheckpointRejected("CAPACITY")
            with zipfile.ZipFile(
                stage / "archive.zip", "x", compression=zipfile.ZIP_STORED
            ) as output:
                output.writestr("manifest.json", raw)
                for name in JOURNALS:
                    output.write(stage / name, arcname=name)
            with (stage / "archive.zip").open("r+b") as stream:
                digest, _ = _digest(stream, MAX_ARCHIVE_BYTES)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(stage / "archive.zip", archive)
            except FileExistsError:
                raise CheckpointRejected("DESTINATION_EXISTS") from None
            return CheckpointResult(state="EXPORTED", archive_sha256=digest, inspection=inspection)
    except CheckpointRejected:
        raise
    except ServiceRejected as exc:
        raise CheckpointRejected(exc.code) from None
    except Exception:
        raise CheckpointRejected("STORAGE_UNAVAILABLE") from None


def _unpack(stream, stage, config):
    with zipfile.ZipFile(stream) as source:
        members = source.infolist()
        if len(members) != 4 or {m.filename for m in members} != {*JOURNALS, "manifest.json"}:
            raise CheckpointRejected("ARCHIVE_INVALID")
        if any(m.compress_type != zipfile.ZIP_STORED or m.flag_bits & 1 for m in members):
            raise CheckpointRejected("ARCHIVE_INVALID")
        if source.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
            raise CheckpointRejected("CAPACITY")
        manifest = SyncCheckpoint.model_validate(
            json.loads(source.read("manifest.json"), object_pairs_hook=_unique)
        )
        if manifest.policy_ref != _policy(config):
            raise CheckpointRejected("POLICY_MISMATCH")
        for commitment in manifest.journals:
            if source.getinfo(commitment.name).file_size != commitment.bytes:
                raise CheckpointRejected("ARCHIVE_INVALID")
            with (
                source.open(commitment.name) as member,
                (stage / commitment.name).open("xb") as target,
            ):
                count, digest = 0, hashlib.sha256()
                while block := member.read(BLOCK):
                    count += len(block)
                    if count > commitment.bytes:
                        raise CheckpointRejected("CAPACITY")
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if count != commitment.bytes or digest.hexdigest() != commitment.sha256:
                raise CheckpointRejected("ARCHIVE_INVALID")
        inspection = _inspect(stage, config)
        if _stable(inspection) != _stable(manifest.inspection):
            raise CheckpointRejected("ARCHIVE_INVALID")
        return inspection


def restore_checkpoint(
    archive: Path, archive_sha256: str, config: ServiceConfig, destination: Path
) -> CheckpointResult:
    archive, destination = archive.absolute(), destination.absolute()
    if destination.exists():
        raise CheckpointRejected("DESTINATION_EXISTS")
    try:
        with archive.open("rb") as stream, _staging(destination.parent) as stage:
            digest, _ = _digest(stream, MAX_ARCHIVE_BYTES)
            if digest != archive_sha256:
                raise CheckpointRejected("ARCHIVE_MISMATCH")
            stream.seek(0)
            inspection = _unpack(stream, stage, config)
            if _digest(stream, MAX_ARCHIVE_BYTES)[0] != archive_sha256:
                raise CheckpointRejected("ARCHIVE_MISMATCH")
            try:
                destination.mkdir()
            except FileExistsError:
                raise CheckpointRejected("DESTINATION_EXISTS") from None
            # The newly reserved directory remains unavailable to cooperating launchers until complete.
            with service_ownership(destination / "service-lock.sqlite"):
                intent = CheckpointPublication(
                    publication_id=uuid4(),
                    archive_sha256=digest,
                    policy_ref=_policy(config),
                    destination_ref=_destination_ref(destination),
                    journals=_commitments(stage),
                )
                with (destination / CHECKPOINT_MARKER).open("xb") as marker:
                    marker.write(intent.model_dump_json().encode("utf-8"))
                    marker.flush()
                    os.fsync(marker.fileno())
                for name in JOURNALS:
                    os.link(stage / name, destination / name)
                (destination / CHECKPOINT_MARKER).unlink()
            return CheckpointResult(state="RESTORED", archive_sha256=digest, inspection=inspection)
    except CheckpointRejected:
        raise
    except (zipfile.BadZipFile, ValueError, KeyError):
        raise CheckpointRejected("ARCHIVE_INVALID") from None
    except Exception:
        # A failed publication retains any owned destination files; never reset/retry partial custody.
        raise CheckpointRejected("STORAGE_UNAVAILABLE") from None


def _read_publication(destination, publication_sha256):
    marker = destination / CHECKPOINT_MARKER
    try:
        if marker.is_symlink() or not marker.is_file():
            raise CheckpointRejected("PUBLICATION_INVALID")
        with marker.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise CheckpointRejected("PUBLICATION_INVALID")
        if hashlib.sha256(raw).hexdigest() != publication_sha256:
            raise CheckpointRejected("PUBLICATION_MISMATCH")
        return CheckpointPublication.model_validate(json.loads(raw, object_pairs_hook=_unique))
    except CheckpointRejected:
        raise
    except Exception:
        raise CheckpointRejected("PUBLICATION_INVALID") from None


def _partial_matches(destination, intent, *, complete=False):
    # The held SQLite ownership connection creates its own rollback sidecar.
    allowed = {*JOURNALS, CHECKPOINT_MARKER, "service-lock.sqlite", "service-lock.sqlite-journal"}
    if any(
        p.name not in allowed or p.is_symlink() or not p.is_file() for p in destination.iterdir()
    ):
        raise CheckpointRejected("DESTINATION_LAYOUT")
    for journal in intent.journals:
        path = destination / journal.name
        if not path.exists():
            if complete:
                raise CheckpointRejected("PARTIAL_JOURNAL_MISMATCH")
            continue
        if path.stat().st_nlink != 1:
            raise CheckpointRejected("DESTINATION_LAYOUT")
        with path.open("rb") as stream:
            digest, count = _digest(stream, MAX_JOURNAL_BYTES)
        if digest != journal.sha256 or count != journal.bytes:
            raise CheckpointRejected("PARTIAL_JOURNAL_MISMATCH")


def finish_restore(
    archive: Path,
    archive_sha256: str,
    config: ServiceConfig,
    destination: Path,
    publication_sha256: str,
) -> RestoreCompletion:
    archive, destination = archive.absolute(), destination.absolute()
    if not destination.is_dir() or not (destination / CHECKPOINT_MARKER).exists():
        raise CheckpointRejected("PUBLICATION_REQUIRED")
    try:
        with (
            service_ownership(destination / "service-lock.sqlite"),
            archive.open("rb") as stream,
            _staging(destination.parent) as stage,
        ):
            intent = _read_publication(destination, publication_sha256)
            if (
                intent.archive_sha256 != archive_sha256
                or intent.policy_ref != _policy(config)
                or intent.destination_ref != _destination_ref(destination)
            ):
                raise CheckpointRejected("PUBLICATION_MISMATCH")
            if _digest(stream, MAX_ARCHIVE_BYTES)[0] != archive_sha256:
                raise CheckpointRejected("ARCHIVE_MISMATCH")
            stream.seek(0)
            inspection = _unpack(stream, stage, config)
            if _digest(stream, MAX_ARCHIVE_BYTES)[0] != archive_sha256:
                raise CheckpointRejected("ARCHIVE_MISMATCH")
            if _commitments(stage) != intent.journals:
                raise CheckpointRejected("PUBLICATION_MISMATCH")
            _partial_matches(destination, intent)
            for name in JOURNALS:
                target = destination / name
                if not target.exists():
                    try:
                        # Remove the owned staging name before admitting single-link custody.
                        os.link(stage / name, target)
                    except FileExistsError:
                        raise CheckpointRejected("PARTIAL_JOURNAL_MISMATCH") from None
                    (stage / name).unlink()
            _partial_matches(destination, intent, complete=True)
            if _read_publication(destination, publication_sha256) != intent:
                raise CheckpointRejected("PUBLICATION_MISMATCH")
            (destination / CHECKPOINT_MARKER).unlink()
            return RestoreCompletion(
                archive_sha256=archive_sha256,
                publication_sha256=publication_sha256,
                publication_id=intent.publication_id,
                inspection=inspection,
            )
    except CheckpointRejected:
        raise
    except ServiceRejected as exc:
        raise CheckpointRejected(exc.code) from None
    except (zipfile.BadZipFile, ValueError, KeyError):
        raise CheckpointRejected("ARCHIVE_INVALID") from None
    except Exception:
        raise CheckpointRejected("STORAGE_UNAVAILABLE") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline demo sync journal checkpoint")
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--state-dir", type=Path, required=True)
    export.add_argument("--archive", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--archive-sha256", required=True)
    restore.add_argument("--destination", type=Path, required=True)
    finish = sub.add_parser("finish-restore")
    finish.add_argument("--archive", type=Path, required=True)
    finish.add_argument("--archive-sha256", required=True)
    finish.add_argument("--destination", type=Path, required=True)
    finish.add_argument("--publication-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, {PASSWORD_ENV: "CHECKPOINT_ONLY"})
        if args.action == "export":
            result = export_checkpoint(args.state_dir, config, args.archive)
        elif args.action == "restore":
            result = restore_checkpoint(args.archive, args.archive_sha256, config, args.destination)
        else:
            result = finish_restore(
                args.archive, args.archive_sha256, config, args.destination, args.publication_sha256
            )
        print(result.model_dump_json(), flush=True)
        return 0
    except (CheckpointRejected, ServiceRejected) as exc:
        print(json.dumps({"state": "WITHHELD", "code": exc.code}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
