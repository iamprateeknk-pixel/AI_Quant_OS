"""Trusted-parent preparation of disposable, source-bound verifier inputs only."""

import os
import json
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from _thread import LockType
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Iterator
from threading import Lock

from ai_quant_os.repair_snapshots import RepairSourceSnapshot, canonical_json
from ai_quant_os.repair_replay import RepairIncidentReplay
from ai_quant_os.repair_locks import repair_root_lock
from ai_quant_os.repair_removals import RemovalJournal


def _reparse(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _cleanup_job(path: Path, parent: Path, identity: tuple[int, int]) -> None:
    current = path.lstat()
    if (
        path.resolve().parent != parent
        or _reparse(current)
        or not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise RuntimeError("Repair job cleanup root identity changed; retained")
    shutil.rmtree(path)


@dataclass(frozen=True)
class PreparedRepairJob:
    path: Path
    snapshot_sha256: str
    suite_sha256: str
    request_sha256: str
    _root_identity: tuple[int, int]
    _expected_files: tuple[tuple[str, bytes], ...]
    _containers: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    _released: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    _container_lock: LockType = field(default_factory=Lock, compare=False, repr=False)

    @property
    def pending_containers(self) -> tuple[str, ...]:
        with self._container_lock:
            return tuple(sorted(self._containers))

    def container_image(self, owner: str) -> str:
        with self._container_lock:
            if owner not in self._containers:
                raise ValueError("Repair job container lease does not match")
            return self._containers[owner]

    def verify_container_lease(self, owner: str) -> None:
        receipt, raw = self._lease(owner, self.container_image(owner))
        self._verify_file(receipt, receipt.lstat(), raw)

    def _lease(self, owner: str, image_id: str) -> tuple[Path, bytes]:
        if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{32}", owner):
            raise ValueError("Repair container owner must be an opaque unique identity")
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("Repair container lease requires a pinned image")
        return (
            self.path.parent / f"{self.path.name}.{owner}.lease.json",
            canonical_json(
                {
                    "schema_version": "1.0",
                    "owner": owner,
                    "image_id": image_id,
                    "root_device": self._root_identity[0],
                    "root_inode": self._root_identity[1],
                    "container_name": "ai-quant-repair-" + owner,
                    "job_path": str(self.path),
                    "snapshot_sha256": self.snapshot_sha256,
                    "suite_sha256": self.suite_sha256,
                    "request_sha256": self.request_sha256,
                }
            ).encode(),
        )

    def hold_container(self, owner: str, *, image_id: str) -> None:
        """Trusted controller must publish ownership before a possibly ambiguous create."""
        self.verify()
        receipt, raw = self._lease(owner, image_id)
        with self._container_lock:
            if self._containers:
                raise ValueError("Repair job already has a pending container")
            with receipt.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            self._containers[owner] = image_id

    def release_container(self, owner: str) -> None:
        """Trusted controller calls only after confirmed removal of its exact container."""
        with self._container_lock:
            if owner not in self._containers:
                raise ValueError("Repair job container lease does not match")
            image_id = self._containers[owner]
            with RemovalJournal(self.path.parent) as journal:
                if journal.confirmed(self, owner, image_id=image_id) is None:
                    raise ValueError("Repair lease release lacks removal acknowledgement")
            receipt, raw = self._lease(owner, image_id)
            try:
                metadata = receipt.lstat()
            except FileNotFoundError:
                metadata = None
            if metadata is not None:
                self._verify_file(receipt, metadata, raw)
                receipt.unlink()
            self._released[owner] = image_id
            del self._containers[owner]

    @property
    def promotion_allowed(self) -> bool:
        return False

    def _stage(self) -> tuple[Path, bytes]:
        return self.path.parent / (self.path.name + ".stage.json"), canonical_json(
            {
                "schema_version": "1.0",
                "job_path": str(self.path),
                "snapshot_sha256": self.snapshot_sha256,
                "suite_sha256": self.suite_sha256,
                "request_sha256": self.request_sha256,
                "root_device": self._root_identity[0],
                "root_inode": self._root_identity[1],
            }
        ).encode()

    def _verify_stage(self) -> None:
        receipt, raw = self._stage()
        metadata = receipt.lstat()
        if _reparse(metadata):
            raise ValueError("Repair staging receipt cannot be a reparse point")
        self._verify_file(receipt, metadata, raw)

    def _release_stage(self) -> None:
        receipt, _ = self._stage()
        try:
            receipt.lstat()
        except FileNotFoundError:
            return  # Historical jobs predate staging receipts.
        self._verify_stage()
        try:
            self.path.lstat()
        except FileNotFoundError:
            receipt.unlink()
            return
        raise ValueError("Repair staging release requires absent inputs")

    def cleanup(self) -> None:
        """Delete only this owned input tree after all container leases are released."""
        if self.pending_containers:
            raise RuntimeError(f"Repair job retained pending container cleanup: {self.path}")
        stage, _ = self._stage()
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            self._verify_stage()
        if self._released:
            with RemovalJournal(self.path.parent) as journal:
                for owner, image_id in self._released.items():
                    if journal.confirmed(self, owner, image_id=image_id) is None:
                        raise ValueError("Repair input cleanup lacks removal acknowledgement")
                self.verify_remaining()
                try:
                    self.path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    _cleanup_job(self.path, self.path.parent, self._root_identity)
                self._release_stage()
                for owner, image_id in self._released.items():
                    journal.cleaned(self, owner, image_id=image_id)
            return
        _cleanup_job(self.path, self.path.parent, self._root_identity)
        self._release_stage()

    def verify(self) -> None:
        """Require the exact owned input tree before a future read-only sandbox mount."""
        root = self.path.lstat()
        if self._inventory(root) != {relative for relative, _ in self._expected_files}:
            raise ValueError("Repair job source inventory is incomplete")

    def verify_remaining(self) -> None:
        """Accept deletion only; every surviving input keeps its exact identity and bytes."""
        try:
            root = self.path.lstat()
        except FileNotFoundError:
            return
        self._inventory(root)

    def _inventory(self, root) -> set[str]:
        if (
            _reparse(root)
            or not stat.S_ISDIR(root.st_mode)
            or (root.st_dev, root.st_ino) != self._root_identity
        ):
            raise ValueError("Repair job root identity changed")
        expected = dict(self._expected_files)
        directories = {".", "src", "src/ai_quant_os"}
        pending = [self.path]
        files = set()
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(self.path).as_posix()
                    # Windows DirEntry.stat omits inode/device/link identity.
                    metadata = path.lstat()
                    if _reparse(metadata):
                        raise ValueError("Repair job cannot contain reparse points")
                    if stat.S_ISDIR(metadata.st_mode):
                        if relative not in directories:
                            raise ValueError("Repair job contains an undeclared directory")
                        pending.append(path)
                    elif relative in expected:
                        self._verify_file(path, metadata, expected[relative])
                        files.add(relative)
                    else:
                        raise ValueError("Repair job contains an undeclared file")
        return files

    @staticmethod
    def _verify_file(path: Path, metadata, expected: bytes) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != len(expected)
        ):
            raise ValueError("Repair job file identity or size changed")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ) or opened.st_nlink != 1:
                raise ValueError("Repair job file identity changed during admission")
            if source.read(len(expected) + 1) != expected:
                raise ValueError("Repair job file bytes changed")
            final = os.fstat(source.fileno())
            if (final.st_size, final.st_mtime_ns, final.st_nlink) != (
                opened.st_size,
                opened.st_mtime_ns,
                1,
            ):
                raise ValueError("Repair job file changed during admission")


def _job_inputs(
    snapshot: RepairSourceSnapshot,
    program: bytes,
    *,
    suite_sha256: str,
    replay: RepairIncidentReplay | None = None,
) -> tuple[bytes, tuple[tuple[str, bytes], ...]]:
    """Copy already-verified inputs; never import, compile or execute the suite/source."""
    if type(snapshot) is not RepairSourceSnapshot:
        raise ValueError("Repair job requires an immutable source snapshot")
    if (
        not isinstance(program, bytes)
        or not 1 <= len(program) <= 65536
        or not isinstance(suite_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", suite_sha256)
        or sha256(program).hexdigest() != suite_sha256
    ):
        raise ValueError("Repair job requires the exact bounded owned suite")
    if replay is not None and (
        type(replay) is not RepairIncidentReplay
        or replay.snapshot_sha256 != snapshot.snapshot_sha256
    ):
        raise ValueError("Repair job replay must bind the exact source snapshot")
    payload = {
        "schema_version": "1.0",
        "candidate_id": snapshot.candidate_id,
        "candidate_commit": snapshot.candidate_commit,
        "rollback_commit": snapshot.rollback_commit,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "suite_sha256": suite_sha256,
        "manifest": snapshot.proposed_manifest,
        "incident": snapshot.incident,
        "inventory": snapshot.inventory,
        "scope": "SANDBOX",
        "promotion_allowed": False,
    }
    if replay is not None:
        payload.update(
            schema_version="1.1",
            replay_sha256=replay.replay_sha256,
            record_sha256=replay.record_sha256,
            replay_kind=replay.kind,
        )
    request = canonical_json(payload).encode()
    if len(request) > 65536:
        raise ValueError("Serialized repair job request exceeds bound")
    expected = tuple(
        sorted(
            [
                *((source.path, source.content) for source in snapshot.files),
                ("verifier.py", program),
                ("request.json", request),
                *(
                    []
                    if replay is None
                    else [("incident-record.json", replay.record_json.encode())]
                ),
            ]
        )
    )
    return request, expected


@contextmanager
def prepared_repair_job(
    snapshot: RepairSourceSnapshot,
    program: bytes,
    *,
    suite_sha256: str,
    root: Path,
    replay: RepairIncidentReplay | None = None,
) -> Iterator[PreparedRepairJob]:
    """Copy already-verified inputs; never import, compile or execute source/program."""
    request, expected = _job_inputs(snapshot, program, suite_sha256=suite_sha256, replay=replay)
    parent = Path(root).resolve()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with repair_root_lock(parent):
        with _staged_repair_job(
            snapshot, suite_sha256=suite_sha256, parent=parent, request=request, expected=expected
        ) as job:
            yield job


@contextmanager
def _staged_repair_job(snapshot, *, suite_sha256, parent, request, expected):
    if next(parent.glob("repair-job-*.*.lease.json"), None) is not None:
        raise ValueError("Repair job root has an unresolved container lease")
    journal_path = parent / "repair-removals.sqlite3"
    try:
        journal_path.lstat()
    except FileNotFoundError:
        pass
    else:
        with RemovalJournal(parent) as journal:
            if journal.unfinished:
                raise ValueError("Repair job root has unfinished journal cleanup")
    if next(parent.glob("repair-job-*"), None) is not None:
        raise ValueError("Repair job root has retained input cleanup")
    path = Path(tempfile.mkdtemp(prefix="repair-job-", dir=parent)).resolve()
    job = None
    root_identity = path.lstat()
    try:
        if path.parent != parent:
            raise ValueError("Repair job directory escaped its owned parent")
        # Non-root sandbox users must be able to read the eventual /job mount.
        # The enforced read-only bind, not host file mode, provides target write isolation.
        path.chmod(0o755)
        job = PreparedRepairJob(
            path,
            snapshot.snapshot_sha256,
            suite_sha256,
            sha256(request).hexdigest(),
            (root_identity.st_dev, root_identity.st_ino),
            expected,
        )
        stage, stage_raw = job._stage()
        with stage.open("xb") as output:
            output.write(stage_raw)
            output.flush()
            os.fsync(output.fileno())
        for relative, content in expected:
            destination = path / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with destination.open("xb") as output:
                output.write(content)
        job._verify_stage()
        job.verify()
        yield job
    finally:
        if job is not None:
            job.cleanup()
        else:
            _cleanup_job(path, parent, (root_identity.st_dev, root_identity.st_ino))


def cleanup_staged_repair_job(
    snapshot: RepairSourceSnapshot,
    program: bytes,
    *,
    suite_sha256: str,
    root: Path,
    stage: Path,
    replay: RepairIncidentReplay | None = None,
) -> dict:
    """Clean source-bound orphan staging only when no container outcome is unresolved."""
    request, expected = _job_inputs(snapshot, program, suite_sha256=suite_sha256, replay=replay)
    parent, receipt = Path(root).resolve(), Path(stage).absolute()
    match = re.fullmatch(r"(repair-job-[a-z0-9_]{1,64})\.stage\.json", receipt.name)
    if receipt.parent.resolve() != parent or match is None:
        raise ValueError("Repair staging receipt boundary invalid")
    with repair_root_lock(parent):
        if next(parent.glob("repair-job-*.*.lease.json"), None) is not None:
            raise ValueError("Repair staging cleanup has unresolved container leases")
        try:
            (parent / "repair-removals.sqlite3").lstat()
        except FileNotFoundError:
            pass
        else:
            with RemovalJournal(parent) as journal:
                if journal.unfinished:
                    raise ValueError("Repair staging cleanup has unfinished container outcomes")
        metadata = receipt.lstat()
        if (
            _reparse(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= 8192
        ):
            raise ValueError("Repair staging receipt identity invalid")
        with receipt.open("rb") as source:
            raw = source.read(8193)
        PreparedRepairJob._verify_file(receipt, metadata, raw)
        value = json.loads(raw)
        if not isinstance(value, dict) or any(
            type(value.get(key)) is not int or value[key] < 0
            for key in ("root_device", "root_inode")
        ):
            raise ValueError("Repair staging root identity invalid")
        job = PreparedRepairJob(
            parent / match[1],
            snapshot.snapshot_sha256,
            suite_sha256,
            sha256(request).hexdigest(),
            (value["root_device"], value["root_inode"]),
            expected,
        )
        job._verify_stage()
        job.verify_remaining()
        try:
            job.path.lstat()
        except FileNotFoundError:
            pass
        else:
            _cleanup_job(job.path, parent, job._root_identity)
        job._release_stage()
        return {
            "schema_version": "1.0",
            "snapshot_sha256": job.snapshot_sha256,
            "state": "STAGING_REMOVED",
            "execution_verified": False,
            "promotion_allowed": False,
        }


def load_retained_repair_job(
    snapshot: RepairSourceSnapshot,
    program: bytes,
    *,
    suite_sha256: str,
    image_id: str,
    root: Path,
    lease: Path,
    replay: RepairIncidentReplay | None = None,
) -> PreparedRepairJob:
    """Reconstruct exact expected bytes from independent pins; read existing files only."""
    request, expected = _job_inputs(snapshot, program, suite_sha256=suite_sha256, replay=replay)
    parent = Path(root).resolve()
    with repair_root_lock(parent):
        return _load_retained_repair_job(
            snapshot,
            suite_sha256=suite_sha256,
            image_id=image_id,
            root=parent,
            lease=lease,
            request=request,
            expected=expected,
        )


def _load_retained_repair_job(snapshot, *, suite_sha256, image_id, root, lease, request, expected):
    parent, receipt = Path(root).resolve(), Path(lease).absolute()
    match = re.fullmatch(r"(repair-job-[a-z0-9_]{1,64})\.([a-f0-9]{32})\.lease\.json", receipt.name)
    if not parent.is_dir() or receipt.parent.resolve() != parent or match is None:
        raise ValueError("Retained repair lease boundary invalid")
    try:
        metadata = receipt.lstat()
    except FileNotFoundError:
        (parent / "repair-removals.sqlite3").lstat()
        with RemovalJournal(parent) as journal:
            raw = journal.acknowledged_lease(match[2])
            job = _job_from_lease(
                snapshot, suite_sha256, image_id, parent, match, request, expected, raw
            )
            if journal.confirmed(job, match[2], image_id=image_id) is None:
                raise ValueError("Repair cleanup acknowledgement binding failed")
        job.verify_remaining()
        return job
    if (
        _reparse(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= 8192
    ):
        raise ValueError("Retained repair lease file identity invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(receipt, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (
            (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise ValueError("Retained repair lease changed while opening")
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError("Retained repair lease exceeds bound")
    PreparedRepairJob._verify_file(receipt, metadata, raw)
    job = _job_from_lease(snapshot, suite_sha256, image_id, parent, match, request, expected, raw)
    job.verify()
    return job


def _job_from_lease(snapshot, suite_sha256, image_id, parent, match, request, expected, raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or any(
        type(value.get(key)) is not int or value[key] < 0 for key in ("root_device", "root_inode")
    ):
        raise ValueError("Retained repair lease root identity invalid")
    job = PreparedRepairJob(
        parent / match[1],
        snapshot.snapshot_sha256,
        suite_sha256,
        sha256(request).hexdigest(),
        (value["root_device"], value["root_inode"]),
        expected,
    )
    _, canonical = job._lease(match[2], image_id)
    if raw != canonical:
        raise ValueError("Retained repair lease binding failed")
    job._containers[match[2]] = image_id
    return job
