"""Trusted-parent preparation of disposable, source-bound verifier inputs only."""

import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterator

from ai_quant_os.repair_snapshots import RepairSourceSnapshot, canonical_json
from ai_quant_os.repair_replay import RepairIncidentReplay


def _reparse(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


@dataclass(frozen=True)
class PreparedRepairJob:
    path: Path
    snapshot_sha256: str
    suite_sha256: str
    request_sha256: str
    _root_identity: tuple[int, int]
    _expected_files: tuple[tuple[str, bytes], ...]

    @property
    def promotion_allowed(self) -> bool:
        return False

    def verify(self) -> None:
        """Require the exact owned input tree before a future read-only sandbox mount."""
        root = self.path.lstat()
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
        if files != set(expected):
            raise ValueError("Repair job source inventory is incomplete")

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


@contextmanager
def prepared_repair_job(
    snapshot: RepairSourceSnapshot,
    program: bytes,
    *,
    suite_sha256: str,
    root: Path,
    replay: RepairIncidentReplay | None = None,
) -> Iterator[PreparedRepairJob]:
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
    parent = Path(root).resolve()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    with tempfile.TemporaryDirectory(prefix="repair-job-", dir=parent) as temporary:
        path = Path(temporary).resolve()
        if path.parent != parent:
            raise ValueError("Repair job directory escaped its owned parent")
        # Non-root sandbox users must be able to read the eventual /job mount.
        # The enforced read-only bind, not host file mode, provides target write isolation.
        path.chmod(0o755)
        for relative, content in expected:
            destination = path / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with destination.open("xb") as output:
                output.write(content)
        metadata = path.lstat()
        job = PreparedRepairJob(
            path,
            snapshot.snapshot_sha256,
            suite_sha256,
            sha256(request).hexdigest(),
            (metadata.st_dev, metadata.st_ino),
            expected,
        )
        job.verify()
        yield job
