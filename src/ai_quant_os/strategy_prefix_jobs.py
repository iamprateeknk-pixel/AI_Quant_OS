"""Owned prefix-only JSON staging and guarded pre-container cleanup; execute nothing."""

from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from ai_quant_os.candidate_invocations import prepare_invocation
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_image_content import encode_metadata, parse_metadata, require_digest
from ai_quant_os.validation_package_contracts import ValidationPackage


LOCK_NAME = ".strategy-owner.lock"
MAX_REQUEST = 524288


def _directory(path: Path) -> tuple[int, int]:
    value = path.lstat()
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    ):
        raise ValueError("Prefix job directory cannot be a link or reparse point")
    return value.st_dev, value.st_ino


def _chain(root: Path) -> tuple[tuple[Path, tuple[int, int]], ...]:
    absolute = Path(root).absolute()
    if ".." in absolute.parts:
        raise ValueError("Prefix root must have an unambiguous absolute path")
    return tuple((path, _directory(path)) for path in (*reversed(absolute.parents), absolute))


def _file_identity(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
        stat.S_IMODE(value.st_mode),
    )


def _persist(path: Path, raw: bytes, mode: int) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


@dataclass(frozen=True)
class PreparedPrefixJob:
    path: Path
    invocation_ref: str
    index: int
    stage_sha256: str
    _request: bytes = field(repr=False)
    _parents: tuple = field(repr=False)
    _root_identity: tuple = field(repr=False)
    _request_identity: tuple = field(repr=False)
    _stage_identity: tuple = field(repr=False)

    @property
    def stage_path(self) -> Path:
        return self.path.parent / (self.path.name + ".stage.json")

    @property
    def execution_verified(self) -> bool:
        return False

    @property
    def promotion_allowed(self) -> bool:
        return False

    def _payload(self, package: ValidationPackage) -> dict:
        return {
            "schema_version": "1.0",
            "state": "PREFIX_STAGED",
            "job_name": self.path.name,
            "parent_path": str(self.path.parent),
            "parent_device": self._parents[-1][1][0],
            "parent_inode": self._parents[-1][1][1],
            "root_device": self._root_identity[0],
            "root_inode": self._root_identity[1],
            "request_device": self._request_identity[0],
            "request_inode": self._request_identity[1],
            "request_bytes": self._request_identity[2],
            "request_mtime_ns": self._request_identity[3],
            "request_links": self._request_identity[4],
            "request_mode": self._request_identity[5],
            "request_sha256": sha256(self._request).hexdigest(),
            "invocation_ref": self.invocation_ref,
            "package_ref": package.package_ref,
            "candidate_ref": package.candidate.candidate_ref,
            "index": self.index,
            "execution_verified": False,
            "promotion_allowed": False,
        }

    def _verify(self, package: ValidationPackage) -> None:
        expected = prepare_invocation(package, self.index)
        if (
            expected.model_dump_json().encode() != self._request
            or expected.invocation_ref != self.invocation_ref
        ):
            raise ValueError("Prefix job differs from the original package")
        if any(_directory(path) != identity for path, identity in self._parents):
            raise ValueError("Prefix parent directory identity changed")
        if self.path.parent != self._parents[-1][0] or _directory(self.path) != self._root_identity:
            raise ValueError("Prefix job root identity changed")
        if os.name == "posix" and stat.S_IMODE(self.path.lstat().st_mode) != 0o755:
            raise ValueError("Prefix job directory permissions changed")
        if {path.name for path in self.path.iterdir()} != {"request.json"}:
            raise ValueError("Prefix job must contain exactly one request")
        request = self.path / "request.json"
        if _file_identity(request) != self._request_identity or request.lstat().st_mode & 0o222:
            raise ValueError("Prefix request identity or read-only mode changed")
        if read_repair_image_input(request, MAX_REQUEST) != self._request:
            raise ValueError("Prefix request bytes changed")
        raw = read_repair_image_input(self.stage_path, 65536)
        if (
            _file_identity(self.stage_path) != self._stage_identity
            or sha256(raw).hexdigest() != self.stage_sha256
            or raw != encode_metadata(self._payload(package))
        ):
            raise ValueError("Prefix stage identity/binding changed")

    def verify(self, package: ValidationPackage) -> None:
        if any(_directory(path) != identity for path, identity in self._parents):
            raise ValueError("Prefix parent directory identity changed")
        with kernel_root_lock(self.path.parent, lock_name=LOCK_NAME):
            self._verify(package)

    def discard_unstarted(self, package: ValidationPackage) -> None:
        """Remove exact inputs only before any container ownership sidecar exists."""
        if any(_directory(path) != identity for path, identity in self._parents):
            raise ValueError("Prefix parent directory identity changed")
        with kernel_root_lock(self.path.parent, lock_name=LOCK_NAME):
            self._verify(package)
            if any(
                path.name.startswith(self.path.name + ".") and path != self.stage_path
                for path in self.path.parent.iterdir()
            ):
                raise ValueError("Prefix job retained for pending or unknown container ownership")
            # Both paths were admitted within the exact ordinary owned directory.
            # Remove only one known file and the now-empty directory; never recurse.
            request = self.path / "request.json"
            if os.name == "nt":
                request.chmod(0o644)
            request.unlink()
            self.path.rmdir()
            self.stage_path.unlink()


def stage_prefix_job(package: ValidationPackage, index: int, *, root: Path) -> PreparedPrefixJob:
    request = prepare_invocation(package, index)
    raw = request.model_dump_json().encode()
    parents = _chain(root)
    parent = parents[-1][0]
    with kernel_root_lock(parent, lock_name=LOCK_NAME):
        if any(_directory(path) != identity for path, identity in parents):
            raise ValueError("Prefix staging parent changed")
        if (
            sum(
                path.name.startswith("strategy-job-") and path.is_dir() for path in parent.iterdir()
            )
            >= 32
        ):
            raise ValueError("Prefix staging capacity exhausted")
        path = parent / ("strategy-job-" + uuid4().hex)
        path.mkdir(mode=0o755)
        path.chmod(0o755)
        _persist(path / "request.json", raw, 0o444)
        initial = PreparedPrefixJob(
            path,
            request.invocation_ref,
            index,
            "",
            raw,
            parents,
            _directory(path),
            _file_identity(path / "request.json"),
            (),
        )
        stage_raw = encode_metadata(initial._payload(package))
        _persist(initial.stage_path, stage_raw, 0o600)
        job = PreparedPrefixJob(
            path,
            request.invocation_ref,
            index,
            sha256(stage_raw).hexdigest(),
            raw,
            parents,
            initial._root_identity,
            initial._request_identity,
            _file_identity(initial.stage_path),
        )
        job._verify(package)
        return job


def load_prefix_job(
    package: ValidationPackage, index: int, *, root: Path, job_name: str, stage_sha256: str
) -> PreparedPrefixJob:
    request = prepare_invocation(package, index)
    require_digest(stage_sha256, prefix="")
    if type(job_name) is not str or not re.fullmatch(r"strategy-job-[a-f0-9]{32}", job_name):
        raise ValueError("Prefix job name must be an owned opaque identity")
    parents = _chain(root)
    parent = parents[-1][0]
    stage = parent / (job_name + ".stage.json")
    raw = read_repair_image_input(stage, 65536)
    if sha256(raw).hexdigest() != stage_sha256:
        raise ValueError("Prefix stage differs from the independent pin")
    payload = parse_metadata(raw)
    if (
        payload.get("parent_path") != str(parent)
        or type(payload.get("parent_device")) is not int
        or type(payload.get("parent_inode")) is not int
        or (payload["parent_device"], payload["parent_inode"]) != parents[-1][1]
    ):
        raise ValueError("Prefix stage parent differs from the independently pinned record")
    with kernel_root_lock(parent, lock_name=LOCK_NAME):
        if read_repair_image_input(stage, 65536) != raw:
            raise ValueError("Prefix stage changed before ownership acquisition")
        fields = (
            "root_device",
            "root_inode",
            "request_device",
            "request_inode",
            "request_bytes",
            "request_mtime_ns",
            "request_links",
            "request_mode",
        )
        if any(type(payload.get(key)) is not int or payload[key] < 0 for key in fields):
            raise ValueError("Prefix stage filesystem metadata invalid")
        job = PreparedPrefixJob(
            parent / job_name,
            request.invocation_ref,
            index,
            stage_sha256,
            request.model_dump_json().encode(),
            parents,
            (payload["root_device"], payload["root_inode"]),
            tuple(payload[key] for key in fields[2:]),
            _file_identity(stage),
        )
        job._verify(package)
        return job
