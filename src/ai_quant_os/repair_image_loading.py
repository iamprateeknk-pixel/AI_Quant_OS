"""Import verified immutable archive bytes; never start or approve a worker."""

import argparse
import json
import re
import shutil
import tarfile
from pathlib import Path

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_image_verification import verify_repair_image_archive_bytes
from ai_quant_os.repair_sandbox import DockerConfinement, local_docker_command


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
        image = _read(
            executable,
            "image",
            "inspect",
            manifest_digest,
            "--platform",
            "linux/amd64",
            "--format",
            "{{json .}}",
        )
        if (
            image.get("Id") != manifest_digest
            or image.get("Os") != "linux"
            or image.get("Architecture") != "amd64"
        ):
            raise ValueError("Imported image identity mismatch")
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(
            "Image load outcome is uncertain; retain archive and inspect the exact pinned ID"
        ) from error
    return {**receipt, "state": "IMAGE_LOAD_INSPECTED", "loaded_image_id": manifest_digest}


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
