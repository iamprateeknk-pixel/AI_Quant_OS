"""Read-only parent inspection of OCI archive hashes; never executes image code."""

import argparse
import gzip
import io
import json
import re
import tarfile
from hashlib import sha256
from pathlib import Path

from ai_quant_os.repair_image_inputs import read_repair_image_input


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate image metadata field")
        value[key] = item
    return value


def _object(raw: bytes) -> dict:
    if len(raw) > 1048576:
        raise ValueError("Image JSON metadata exceeds bound")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError("Image JSON metadata must be an object")
    return value


def _digest(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def _members(raw: bytes) -> dict[str, bytes]:
    members = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if (
                len(members) >= 32
                or not member.isfile()
                or member.pax_headers
                or member.name in members
            ):
                raise ValueError("Unsupported or duplicate OCI archive member")
            if member.name not in ("oci-layout", "index.json") and not re.fullmatch(
                r"blobs/sha256/[a-f0-9]{64}", member.name
            ):
                raise ValueError("Unexpected OCI archive path")
            if not 0 < member.size <= 268435456:
                raise ValueError("OCI member exceeds bound")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Missing OCI member bytes")
            with stream:
                content = stream.read(member.size + 1)
            if len(content) != member.size:
                raise ValueError("Incomplete OCI member")
            members[member.name] = content
        if any(raw[archive.offset :]):
            raise ValueError("Unexpected bytes after OCI tar members")
    if not {"oci-layout", "index.json"} <= members.keys():
        raise ValueError("Missing OCI layout/index")
    for name, content in members.items():
        if name.startswith("blobs/") and sha256(content).hexdigest() != name.rsplit("/", 1)[1]:
            raise ValueError("OCI blob hash mismatch")
    return members


def _blob(members: dict[str, bytes], descriptor: dict, media_type: str) -> bytes:
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("mediaType") != media_type
        or not isinstance(descriptor.get("digest"), str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", descriptor["digest"])
        or type(descriptor.get("size")) is not int
    ):
        raise ValueError("Invalid OCI descriptor")
    name = "blobs/sha256/" + descriptor["digest"][7:]
    raw = members.get(name)
    if raw is None or descriptor["size"] != len(raw):
        raise ValueError("Missing OCI blob or descriptor size mismatch")
    return raw


def _base_profile(recipes: Path) -> tuple[dict, dict]:
    pin = _object(read_repair_image_input(recipes / "base_image.json", 1048576))
    if (
        pin.get("schema_version") != "1.0"
        or pin.get("state") != "BASE_METADATA_ONLY"
        or pin.get("repository") != "docker.io/library/python"
        or pin.get("platform") != "linux/amd64"
        or any(
            pin.get(key) is not False
            for key in ("image_verified", "execution_verified", "promotion_allowed")
        )
    ):
        raise ValueError("Unsupported server base profile")
    raw = read_repair_image_input(recipes / "base_manifest.json", 1048576)
    if _digest(raw) != pin.get("manifest_digest"):
        raise ValueError("Server base manifest hash mismatch")
    manifest = _object(raw)
    config = read_repair_image_input(recipes / "base_config.json", 1048576)
    if (
        _digest(config) != pin.get("config_digest")
        or not isinstance(manifest.get("config"), dict)
        or manifest["config"].get("digest") != pin["config_digest"]
        or manifest.get("layers") != pin.get("layers")
    ):
        raise ValueError("Server base configuration binding mismatch")
    return pin, _object(config)


def _expanded_hash(raw: bytes, media_type: str) -> str:
    if media_type == "application/vnd.oci.image.layer.v1.tar":
        return _digest(raw)
    digest, count = sha256(), 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        while chunk := stream.read(1048576):
            count += len(chunk)
            if count > 268435456:
                raise ValueError("Expanded layer exceeds bound")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _runtime(config: dict, base_config: dict) -> None:
    runtime = config.get("config")
    if (
        config.get("os") != "linux"
        or config.get("architecture") != "amd64"
        or not isinstance(runtime, dict)
    ):
        raise ValueError("Worker runtime platform is not supported")
    required = {
        "User": "65534:65534",
        "WorkingDir": "/scratch",
        "Entrypoint": ["/usr/bin/env"],
        "Cmd": ["/usr/local/bin/python", "-I", "-B"],
    }
    if any(runtime.get(key) != expected for key, expected in required.items()):
        raise ValueError("Worker runtime profile mismatch")
    labels = runtime.get("Labels")
    required_labels = {
        "ai.quant.repair.state": "OCI_ARCHIVE_ONLY",
        "ai.quant.repair.execution-verified": "false",
        "ai.quant.repair.promotion-allowed": "false",
    }
    if not isinstance(labels, dict) or any(
        labels.get(key) != expected for key, expected in required_labels.items()
    ):
        raise ValueError("Archive labels cannot grant worker authority")
    base_runtime = base_config.get("config")
    if (
        not isinstance(base_runtime, dict)
        or not isinstance(base_runtime.get("Env"), list)
        or not all(isinstance(entry, str) for entry in base_runtime["Env"])
    ):
        raise ValueError("Server base environment must contain strings")
    environment = [
        entry for entry in base_runtime["Env"] if not entry.startswith("PYTHONDONTWRITEBYTECODE=")
    ]
    if runtime.get("Env") != [*environment, "PYTHONDONTWRITEBYTECODE=1"]:
        raise ValueError("Worker environment differs from server base profile")


def verify_repair_image_archive(
    archive: Path, *, recipes: Path, archive_sha256: str, manifest_digest: str, config_digest: str
) -> dict:
    """Read a bounded ordinary archive and verify explicit independent pins."""
    return verify_repair_image_archive_bytes(
        read_repair_image_input(archive, 536870912),
        recipes=recipes,
        archive_sha256=archive_sha256,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
    )


def verify_repair_image_archive_bytes(
    raw: bytes, *, recipes: Path, archive_sha256: str, manifest_digest: str, config_digest: str
) -> dict:
    """Verify explicit operator pins and structure without creating files or running code.

    Pins/base profile must come from trusted independent review, never the archive.
    Hash agreement is transport/inventory evidence and grants no execution authority.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 536870912:
        raise ValueError("Image archive requires bounded immutable bytes")
    for value, pattern in (
        (archive_sha256, r"[a-f0-9]{64}"),
        (manifest_digest, r"sha256:[a-f0-9]{64}"),
        (config_digest, r"sha256:[a-f0-9]{64}"),
    ):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("Independent archive pins are required")
    if sha256(raw).hexdigest() != archive_sha256:
        raise ValueError("Archive hash differs from independent pin")
    members = _members(raw)
    if _object(members["oci-layout"]) != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported OCI layout")
    index = _object(members["index.json"])
    descriptors = index.get("manifests")
    if (
        type(index.get("schemaVersion")) is not int
        or index["schemaVersion"] != 2
        or not isinstance(descriptors, list)
        or len(descriptors) != 1
        or not isinstance(descriptors[0], dict)
        or descriptors[0].get("digest") != manifest_digest
        or descriptors[0].get("platform") != {"os": "linux", "architecture": "amd64"}
    ):
        raise ValueError("OCI index differs from independent worker pin")
    manifest = _object(_blob(members, descriptors[0], "application/vnd.oci.image.manifest.v1+json"))
    descriptor = manifest.get("config")
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != 2
        or not isinstance(descriptor, dict)
        or descriptor.get("digest") != config_digest
    ):
        raise ValueError("Manifest/config differs from independent worker pin")
    config = _object(_blob(members, descriptor, "application/vnd.oci.image.config.v1+json"))
    pin, base_config = _base_profile(recipes)
    _runtime(config, base_config)
    layers, rootfs = manifest.get("layers"), config.get("rootfs")
    base_rootfs = base_config.get("rootfs")
    if (
        not isinstance(pin.get("layers"), list)
        or not isinstance(layers, list)
        or len(layers) != len(pin["layers"]) + 1
        or not 2 <= len(layers) <= 17
        or layers[:-1] != pin["layers"]
        or not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(rootfs.get("diff_ids"), list)
        or len(rootfs["diff_ids"]) != len(layers)
        or not isinstance(base_rootfs, dict)
        or rootfs["diff_ids"][:-1] != base_rootfs.get("diff_ids")
    ):
        raise ValueError("Worker rootfs differs from pinned base")
    expected_members = {
        "oci-layout",
        "index.json",
        "blobs/sha256/" + manifest_digest[7:],
        "blobs/sha256/" + config_digest[7:],
    }
    for position, (layer, diff_id) in enumerate(zip(layers, rootfs["diff_ids"], strict=True)):
        media_type = (
            "application/vnd.oci.image.layer.v1.tar"
            if position == len(layers) - 1
            else "application/vnd.oci.image.layer.v1.tar+gzip"
        )
        content = _blob(members, layer, media_type)
        if _expanded_hash(content, media_type) != diff_id:
            raise ValueError("Worker expanded layer hash mismatch")
        expected_members.add("blobs/sha256/" + layer["digest"][7:])
    if set(members) != expected_members:
        raise ValueError("Unreferenced OCI blob is not accepted")
    return {
        "schema_version": "1.0",
        "state": "ARCHIVE_HASHES_VERIFIED",
        "archive_sha256": archive_sha256,
        "archive_bytes": len(raw),
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "base_reference": pin["repository"] + "@" + pin["manifest_digest"],
        "layer_count": len(layers),
        "archive_integrity_verified": True,
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect pinned OCI archive bytes without a daemon"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--recipes", required=True, type=Path)
    for name in ("archive-sha256", "manifest-digest", "config-digest"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        receipt = verify_repair_image_archive(
            args.archive,
            recipes=args.recipes,
            archive_sha256=args.archive_sha256,
            manifest_digest=args.manifest_digest,
            config_digest=args.config_digest,
        )
    except (OSError, ValueError, RuntimeError, tarfile.TarError):
        parser.exit(
            2, "Archive rejected; verify independent pins, server profile and byte integrity.\n"
        )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
