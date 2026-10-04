"""Pinned OCI handoff to the fixed local engine; never create/start a container."""

import argparse
from hashlib import sha256
import io
from pathlib import Path
import shutil
import tarfile

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.repair_sandbox import local_docker_command
from ai_quant_os.strategy_image_content import MAX_ARCHIVE, encode_metadata, parse_metadata
from ai_quant_os.strategy_image_runtime import (
    StrategyImageHandoff,
    require_loaded_strategy_image,
    require_strategy_image_engine,
)
from ai_quant_os.strategy_image_verification import verify_strategy_worker_image_bytes


def _read(executable: str, *arguments: str) -> dict:
    result = local_docker_command(executable, *arguments)
    if result.returncode:
        raise ValueError("Strategy image metadata unavailable")
    return parse_metadata(result.stdout)


def _handoff(archive: Path, *, load: bool, **pins) -> StrategyImageHandoff:
    raw = read_repair_image_input(archive, MAX_ARCHIVE)
    receipt = verify_strategy_worker_image_bytes(raw, **pins)
    # Re-read only bounded JSON from the already verified immutable snapshot.
    # No member is extracted into a host directory or imported as Python.
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as content:
        with content.extractfile("blobs/sha256/" + receipt.config_digest[7:]) as stream:
            config = parse_metadata(stream.read(1048577))
        manifest = content.getmember("blobs/sha256/" + receipt.manifest_digest[7:])
        descriptor = {
            "digest": receipt.manifest_digest,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "size": manifest.size,
        }
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("Local Docker executable unavailable")
    engine = _read(executable, "info", "--format", "{{json .}}")
    version = _read(executable, "version", "--format", "{{json .Server}}")
    require_strategy_image_engine(engine, version)
    try:
        if load:
            result = local_docker_command(
                executable,
                "image",
                "load",
                "--quiet",
                "--platform",
                "linux/amd64",
                input_bytes=raw,
                timeout=60,
            )
            if result.returncode:
                raise ValueError("Strategy image load not acknowledged")
        image = _read(
            executable,
            "image",
            "inspect",
            receipt.manifest_digest,
            "--platform",
            "linux/amd64",
            "--format",
            "{{json .}}",
        )
        require_loaded_strategy_image(image, config=config, descriptor=descriptor)
    except (OSError, ValueError, RuntimeError) as error:
        if load:
            raise RuntimeError(
                "Strategy image load outcome uncertain; retain archive and inspect the pinned identity independently"
            ) from error
        raise ValueError("Strategy image presence inspection refused") from error
    return StrategyImageHandoff(
        state="IMAGE_LOAD_INSPECTED" if load else "IMAGE_PRESENT_INSPECTED",
        archive=receipt,
        loaded_image_id=image["Id"],
        engine_id=engine["ID"],
        api_version=version["ApiVersion"],
        engine_metadata_sha256=sha256(
            encode_metadata({"info": engine, "version": version})
        ).hexdigest(),
        image_metadata_sha256=sha256(encode_metadata(image)).hexdigest(),
    )


def load_strategy_worker_image(archive: Path, **pins) -> StrategyImageHandoff:
    """Verify once, send those exact bytes once, then inspect; never retry mutations."""
    return _handoff(archive, load=True, **pins)


def inspect_strategy_worker_image(archive: Path, **pins) -> StrategyImageHandoff:
    """Verify local archive and inspect existing engine/image; no daemon mutation."""
    return _handoff(archive, load=False, **pins)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load or inspect a pinned strategy image; no container execution"
    )
    parser.add_argument("command", choices=("load", "inspect"))
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--base-metadata", required=True, type=Path)
    for field in (
        "base-manifest-digest",
        "worker-sha256",
        "archive-sha256",
        "manifest-digest",
        "config-digest",
    ):
        parser.add_argument("--" + field, required=True)
    arguments = vars(parser.parse_args())
    command = arguments.pop("command")
    try:
        receipt = (
            load_strategy_worker_image if command == "load" else inspect_strategy_worker_image
        )(**arguments)
    except (OSError, ValueError, RuntimeError, EOFError, tarfile.TarError):
        parser.exit(
            2,
            "Strategy image handoff refused or uncertain; retain archive and inspect independently.\n",
        )
    print(receipt.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
