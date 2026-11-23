"""Import verified immutable archive bytes; never start or approve a worker."""

import argparse
import json
import os
import re
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_image_verification import verify_repair_image_archive_bytes
from ai_quant_os.repair_sandbox import DockerConfinement, local_docker_command


def _journal_path(archive: Path, archive_sha256: str) -> Path:
    return archive.with_name(f"{archive.name}.{archive_sha256[:16]}.import-journal.json")


def _write_journal(path: Path, identity: dict, state: str) -> None:
    payload = json.dumps({**identity, "state": state}, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Image import journal cannot be a symbolic link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_journal(path: Path, identity: dict) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError("Image import journal is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Image import journal is unreadable") from error
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in identity.items()):
        raise ValueError("Image import journal identity mismatch")
    state = value.get("state")
    if state not in {"LOAD_STARTED", "UNCERTAIN", "IMAGE_LOAD_INSPECTED"}:
        raise ValueError("Image import journal state is invalid")
    return state


def _inspect_pinned_image(executable: str, manifest_digest: str) -> dict:
    return _read(
        executable,
        "image",
        "inspect",
        manifest_digest,
        "--platform",
        "linux/amd64",
        "--format",
        "{{json .}}",
    )


def _image_matches(image: dict, manifest_digest: str) -> bool:
    return (
        image.get("Id") == manifest_digest
        and image.get("Os") == "linux"
        and image.get("Architecture") == "amd64"
    )


@contextmanager
def _journal_lock(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    if lock_path.is_symlink():
        raise ValueError("Image import journal lock cannot be a symbolic link")
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _load_or_reconcile(
    *,
    executable: str,
    content: bytes,
    receipt: dict,
    identity: dict,
    journal: Path,
    manifest_digest: str,
) -> dict:
    prior_state = _read_journal(journal, identity)
    if prior_state is not None:
        try:
            image = _inspect_pinned_image(executable, manifest_digest)
        except (OSError, ValueError, RuntimeError) as error:
            _write_journal(journal, identity, "UNCERTAIN")
            raise RuntimeError(
                "Image load outcome is uncertain; retain archive and inspect the exact pinned ID"
            ) from error
        if not _image_matches(image, manifest_digest):
            _write_journal(journal, identity, "UNCERTAIN")
            raise RuntimeError(
                "Image load outcome is uncertain; retain archive and inspect the exact pinned ID"
            )
        _write_journal(journal, identity, "IMAGE_LOAD_INSPECTED")
        return {**receipt, "state": "IMAGE_LOAD_INSPECTED", "loaded_image_id": manifest_digest}

    _write_journal(journal, identity, "LOAD_STARTED")
    try:
        result = local_docker_command(
            executable,
            "image",
            "load",
            "--quiet",
            "--platform",
            "linux/amd64",
            input_bytes=content,
            timeout=60,
        )
        if result.returncode:
            raise ValueError("Image import did not acknowledge success")
        image = _inspect_pinned_image(executable, manifest_digest)
        if not _image_matches(image, manifest_digest):
            raise ValueError("Imported image identity mismatch")
    except (OSError, ValueError, RuntimeError) as error:
        _write_journal(journal, identity, "UNCERTAIN")
        raise RuntimeError(
            "Image load outcome is uncertain; retain archive and inspect the exact pinned ID"
        ) from error
    _write_journal(journal, identity, "IMAGE_LOAD_INSPECTED")
    return {**receipt, "state": "IMAGE_LOAD_INSPECTED", "loaded_image_id": manifest_digest}


def _read(executable: str, *arguments: str) -> dict:
    result = local_docker_command(executable, *arguments)
    if result.returncode:
        raise ValueError("Local engine metadata unavailable")
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        raise ValueError("Local engine metadata must be an object")
    return metadata


def load_repair_image_archive(
    archive: Path, *, recipes: Path, archive_sha256: str, manifest_digest: str, config_digest: str
) -> dict:
    """Verify, import once and inspect the exact manifest ID, granting no authority.

    The archive is read once into immutable bytes before any daemon command. A
    timeout/failure after sending load is uncertain: retain inputs and inspect
    independently; do not retry, delete shared images or start a container here.
    """
    content = read_repair_image_input(archive, 536870912)
    receipt = verify_repair_image_archive_bytes(
        content,
        recipes=recipes,
        archive_sha256=archive_sha256,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
    )
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Local Docker executable unavailable")
    engine = _read(executable, "info", "--format", "{{json .}}")
    DockerConfinement.require_engine(engine)
    drivers = engine.get("DriverStatus")
    if (
        not isinstance(drivers, list)
        or ["driver-type", "io.containerd.snapshotter.v1"] not in drivers
    ):
        raise ValueError("OCI import requires the containerd image store")
    version = _read(executable, "version", "--format", "{{json .Server}}")
    api_version = version.get("ApiVersion")
    if (
        not isinstance(api_version, str)
        or not re.fullmatch(r"1\.[0-9]{1,3}", api_version)
        or int(api_version.split(".")[1]) < 49
    ):
        raise ValueError("Image handoff requires platform inspection API 1.49 or later")
    journal = _journal_path(archive, receipt["archive_sha256"])
    identity = {
        "schema_version": 1,
        "archive_sha256": receipt["archive_sha256"],
        "manifest_digest": receipt["manifest_digest"],
        "config_digest": receipt["config_digest"],
        "engine_store": "local-containerd-linux-amd64",
    }
    with _journal_lock(journal):
        return _load_or_reconcile(
            executable=executable,
            content=content,
            receipt=receipt,
            identity=identity,
            journal=journal,
            manifest_digest=manifest_digest,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import a pinned archive without starting a worker"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--recipes", required=True, type=Path)
    for name in ("archive-sha256", "manifest-digest", "config-digest"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        receipt = load_repair_image_archive(
            args.archive,
            recipes=args.recipes,
            archive_sha256=args.archive_sha256,
            manifest_digest=args.manifest_digest,
            config_digest=args.config_digest,
        )
    except (OSError, ValueError, RuntimeError, EOFError, tarfile.TarError):
        parser.exit(
            2, "Image import refused or uncertain; retain archive and inspect independently.\n"
        )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
