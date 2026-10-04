"""Archive before exact removed-job deletion, with independently pinned partial recovery."""

import base64
from hashlib import sha256
import os
from pathlib import Path
import re

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.root_ownership import kernel_root_lock
from ai_quant_os.strategy_container_journal import StrategyContainerJournal
from ai_quant_os.strategy_container_lifecycle import StrategyContainerPlan, _verify
from ai_quant_os.strategy_image_content import encode_metadata, require_digest
from ai_quant_os.strategy_prefix_execution import restore_strategy_prefix_execution
from ai_quant_os.strategy_prefix_jobs import (
    LOCK_NAME,
    PreparedPrefixJob,
    _chain,
    _directory,
    _file_identity,
)
from ai_quant_os.strategy_prefix_release_binding import require_release_package
from ai_quant_os.strategy_prefix_release_contracts import (
    FILES,
    PrefixReleaseDirectory,
    PrefixReleaseFile,
    StrategyPrefixReleaseBundle,
    StrategyPrefixReleaseResult,
)
from ai_quant_os.validation_package_contracts import ValidationPackage

MAX_RELEASE = 2097152


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _sidecar(job, kind):
    return job.path.parent / (job.path.name + "." + kind + ".json")


def _file(job, name):
    return (
        job.path / name
        if name == "request.json"
        else job.path.parent / (job.path.name + "." + name)
    )


def _inventory(job, *, release_files=()):
    allowed = {_file(job, name) for name in FILES if name != "request.json"}
    allowed.update(_sidecar(job, name) for name in release_files)
    actual = {
        path for path in job.path.parent.iterdir() if path.name.startswith(job.path.name + ".")
    }
    if not actual <= allowed:
        raise ValueError("Unknown prefix ownership sidecar prevents release")
    if _exists(job.path):
        _directory(job.path)
    if _exists(job.path) and {path.name for path in job.path.iterdir()} - {"request.json"}:
        raise ValueError("Unknown prefix job file prevents release")


def _publish(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _durable_marker(path, raw):
    if _exists(path):
        if read_repair_image_input(path, MAX_RELEASE) != raw:
            raise ValueError("Release marker differs from exact pinned operation")
        identity = _file_identity(path)
        with path.open("r+b") as stream:
            if (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) != identity[:2]:
                raise ValueError("Release marker changed before sync")
            os.fsync(stream.fileno())
        if _file_identity(path) != identity:
            raise ValueError("Release marker changed while syncing")
    else:
        _publish(path, raw)


def _removed(bundle, root):
    with StrategyContainerJournal(root) as journal:
        if journal.current(bundle.receipt.ownership) != bundle.receipt.removal:
            raise ValueError("Release requires exact audited container removal")


def prepare_strategy_prefix_release(
    plan: StrategyContainerPlan,
    job: PreparedPrefixJob,
    package: ValidationPackage,
    *,
    receipt_sha256: str,
) -> StrategyPrefixReleaseBundle:
    receipt = restore_strategy_prefix_execution(plan, job, package, receipt_sha256=receipt_sha256)
    if _chain(job.path.parent) != job._parents:
        raise ValueError("Prefix parent changed before release preparation")
    with kernel_root_lock(job.path.parent, lock_name=LOCK_NAME):
        job._verify(package)
        _verify(plan, job)
        _inventory(job)
        files = []
        for name, limit in sorted(FILES.items()):
            path = _file(job, name)
            identity = _file_identity(path)
            raw = read_repair_image_input(path, limit)
            if _file_identity(path) != identity:
                raise ValueError("Prefix custody changed during release capture")
            files.append(
                PrefixReleaseFile(
                    name=name,
                    identity=identity,
                    sha256=sha256(raw).hexdigest(),
                    data_base64=base64.b64encode(raw).decode(),
                )
            )
        bundle = StrategyPrefixReleaseBundle(
            package_ref=package.package_ref,
            job_name=job.path.name,
            index=job.index,
            parents=tuple(
                PrefixReleaseDirectory(path=str(path), identity=identity)
                for path, identity in job._parents
            ),
            job_identity=job._root_identity,
            files=tuple(files),
            receipt_sha256=receipt_sha256,
            receipt=receipt,
        )
        require_release_package(bundle, package)
        _removed(bundle, job.path.parent)
        raw = bundle.model_dump_json().encode()
        if len(raw) > MAX_RELEASE:
            raise ValueError("Prefix recovery archive exceeds byte bound")
        _publish(_sidecar(job, "release"), raw)
        job._verify(package)
        return bundle


def _present_files(bundle, job, *, attempted):
    present = []
    for row in bundle.files:
        path = _file(job, row.name)
        try:
            identity = _file_identity(path)
        except FileNotFoundError:
            if not attempted:
                raise ValueError("Prefix input missing without a release attempt") from None
            continue
        expected = row.identity
        if attempted and os.name == "nt" and row.name == "request.json":
            expected = (
                (*expected[:5], identity[5])
                if identity[5] in (row.identity[5], 0o666)
                else expected
            )
        if identity != expected or read_repair_image_input(path, FILES[row.name]) != row.content():
            raise ValueError("Retained prefix file differs from release archive")
        present.append((path, identity))
    return present


def release_strategy_prefix_inputs(
    package: ValidationPackage,
    *,
    root: Path,
    job_name: str,
    release_sha256: str,
) -> StrategyPrefixReleaseResult:
    require_digest(release_sha256, prefix="")
    # Validate the opaque basename before constructing any selected path.
    if type(job_name) is not str or not re.fullmatch(r"strategy-job-[a-f0-9]{32}", job_name):
        raise ValueError("Owned prefix job name required")
    parents = _chain(root)
    parent = parents[-1][0]
    archive = parent / (job_name + ".release.json")
    raw = read_repair_image_input(archive, MAX_RELEASE)
    if sha256(raw).hexdigest() != release_sha256:
        raise ValueError("Release archive differs from independent pin")
    bundle = StrategyPrefixReleaseBundle.model_validate_json(raw)
    job = require_release_package(bundle, package)
    if (
        raw != bundle.model_dump_json().encode()
        or job.path.parent != parent
        or job.path.name != job_name
        or job._parents != parents
    ):
        raise ValueError("Release archive belongs to a different root or identity")
    with kernel_root_lock(parent, lock_name=LOCK_NAME):
        if _chain(parent) != parents or read_repair_image_input(archive, MAX_RELEASE) != raw:
            raise ValueError("Release root or archive changed before custody")
        _removed(bundle, parent)
        _inventory(job, release_files=("release", "release-attempt", "released"))
        attempt = _sidecar(job, "release-attempt")
        attempt_raw = encode_metadata(
            {
                "release_sha256": release_sha256,
                "state": "PREFIX_RELEASE_MAY_HAVE_STARTED",
                "execution_verified": False,
                "promotion_allowed": False,
            }
        )
        attempted = _exists(attempt)
        if attempted and read_repair_image_input(attempt, 8192) != attempt_raw:
            raise ValueError("Prefix release attempt differs from pinned archive")
        if _exists(job.path):
            if _directory(job.path) != bundle.job_identity:
                raise ValueError("Prefix release directory identity changed")
        elif not attempted:
            raise ValueError("Prefix job missing without release attempt")
        present = _present_files(bundle, job, attempted=attempted)
        result = StrategyPrefixReleaseResult(
            release_sha256=release_sha256,
            receipt_sha256=bundle.receipt_sha256,
            owner=bundle.receipt.ownership.owner,
            container_id=bundle.receipt.exit.container_id,
            package_ref=bundle.package_ref,
            invocation_ref=bundle.receipt.ownership.invocation_ref,
        )
        final = _sidecar(job, "released")
        if _exists(final) and (present or _exists(job.path)):
            raise ValueError("Released prefix inputs unexpectedly reappeared")
        # Fsync the retained archive again before destructive work or recovery.
        _durable_marker(archive, raw)
        _durable_marker(attempt, attempt_raw)
        request = job.path / "request.json"
        for path, identity in present:
            if path != request:
                continue
            if _file_identity(path) != identity:
                raise ValueError("Prefix file changed before exact release")
            if os.name == "nt":
                path.chmod(0o644)
            path.unlink()
        if _exists(job.path):
            if _directory(job.path) != bundle.job_identity:
                raise ValueError("Prefix directory changed before removal")
            job.path.rmdir()
        for path, identity in present:
            if path == request:
                continue
            if _file_identity(path) != identity:
                raise ValueError("Prefix sidecar changed before exact release")
            path.unlink()
        _durable_marker(final, result.model_dump_json().encode())
        return result
