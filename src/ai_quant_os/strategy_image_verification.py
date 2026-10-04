"""Read-only structural/hash verification of static worker images; execute nothing."""

from copy import deepcopy
from hashlib import sha256
import io
from pathlib import Path
import re
import tarfile

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import (
    CMD,
    LABELS,
    MAX_ARCHIVE,
    MAX_EXPANDED,
    MAX_LAYER,
    check_base_blob,
    encode_metadata,
    parse_metadata,
    read_strategy_image_base,
    require_descriptor,
    require_digest,
)
from ai_quant_os.strategy_image_contracts import ImageSourcePin, StrategyWorkerImageReceipt


def _members(raw: bytes) -> dict[str, bytes]:
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if (
                len(result) >= 24
                or member.type != tarfile.REGTYPE
                or member.pax_headers
                or member.linkname
                or member.name in result
                or (member.mode, member.uid, member.gid, member.mtime, member.uname, member.gname)
                != (0o644, 0, 0, 0, "", "")
            ):
                raise ValueError("Unexpected worker OCI member metadata")
            if member.name not in ("oci-layout", "index.json") and not re.fullmatch(
                r"blobs/sha256/[a-f0-9]{64}", member.name
            ):
                raise ValueError("Unexpected worker OCI path")
            if not 0 < member.size <= MAX_LAYER:
                raise ValueError("Worker OCI member exceeds bound")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Missing worker OCI content")
            with stream:
                content = stream.read(member.size + 1)
            if len(content) != member.size:
                raise ValueError("Incomplete worker OCI content")
            result[member.name] = content
        if any(raw[archive.offset :]):
            raise ValueError("Unexpected worker OCI trailing content")
    if not {"oci-layout", "index.json"} <= result.keys():
        raise ValueError("Worker OCI layout/index missing")
    for name, content in result.items():
        if name.startswith("blobs/") and sha256(content).hexdigest() != name.rsplit("/", 1)[1]:
            raise ValueError("Worker OCI blob hash mismatch")
    return result


def _blob(members: dict, descriptor: dict, media_type: str, maximum: int) -> bytes:
    require_descriptor(descriptor, media_type, maximum)
    raw = members.get("blobs/sha256/" + descriptor["digest"][7:])
    if raw is None or len(raw) != descriptor["size"]:
        raise ValueError("Missing worker OCI blob or size mismatch")
    return raw


def _worker_layer(raw: bytes, worker_sha256: str) -> None:
    names = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            names.append(member.name)
            if (
                len(names) > 4
                or member.pax_headers
                or member.linkname
                or (member.uid, member.gid, member.mtime, member.uname, member.gname)
                != (0, 0, 0, "", "")
            ):
                raise ValueError("Unexpected worker layer metadata")
            if member.name == "opt/strategy/worker.py":
                if (
                    member.type != tarfile.REGTYPE
                    or member.mode != 0o444
                    or not 0 < member.size <= 65536
                ):
                    raise ValueError("Worker layer source mode/type/bound mismatch")
                with archive.extractfile(member) as stream:
                    source = stream.read(65537)
                if len(source) != member.size or sha256(source).hexdigest() != worker_sha256:
                    raise ValueError("Worker layer differs from independent source pin")
            elif (
                member.name not in ("opt", "opt/strategy", "scratch")
                or member.type != tarfile.DIRTYPE
                or member.mode != 0o755
                or member.size
            ):
                raise ValueError("Unexpected worker layer path/type")
        if names != ["opt", "opt/strategy", "opt/strategy/worker.py", "scratch"] or any(
            raw[archive.offset :]
        ):
            raise ValueError("Worker layer inventory/trailing content mismatch")


def verify_strategy_worker_image(
    archive: Path,
    *,
    base_metadata: Path,
    base_manifest_digest: str,
    worker_sha256: str,
    archive_sha256: str,
    manifest_digest: str,
    config_digest: str,
) -> StrategyWorkerImageReceipt:
    for value in (base_manifest_digest, manifest_digest, config_digest):
        require_digest(value)
    for value in (worker_sha256, archive_sha256):
        require_digest(value, prefix="")
    base = read_strategy_image_base(base_metadata, base_manifest_digest)
    raw = read_repair_image_input(archive, MAX_ARCHIVE)
    if sha256(raw).hexdigest() != archive_sha256:
        raise ValueError("Worker archive differs from independent archive pin")
    members = _members(raw)
    if parse_metadata(members["oci-layout"]) != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported worker OCI layout")
    index = parse_metadata(members["index.json"])
    if (
        index.get("schemaVersion") != 2
        or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
        or type(index.get("manifests")) is not list
        or len(index["manifests"]) != 1
    ):
        raise ValueError("Unsupported worker OCI index")
    selection = index["manifests"][0]
    if (
        type(selection) is not dict
        or selection.get("digest") != manifest_digest
        or selection.get("platform") != {"os": "linux", "architecture": "amd64"}
    ):
        raise ValueError("Worker manifest/platform differs from independent pin")
    manifest = parse_metadata(
        _blob(members, selection, "application/vnd.oci.image.manifest.v1+json", 1048576)
    )
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
        or type(manifest.get("layers")) is not list
        or len(manifest["layers"]) != len(base.layers) + 1
        or manifest["layers"][:-1] != list(base.layers)
    ):
        raise ValueError("Worker base layer inventory mismatch")
    descriptor = manifest.get("config")
    if type(descriptor) is not dict or descriptor.get("digest") != config_digest:
        raise ValueError("Worker config differs from independent pin")
    config = parse_metadata(
        _blob(members, descriptor, "application/vnd.oci.image.config.v1+json", 1048576)
    )
    layer = manifest["layers"][-1]
    layer_raw = _blob(members, layer, "application/vnd.oci.image.layer.v1.tar", 131072)
    _worker_layer(layer_raw, worker_sha256)
    expected = deepcopy(base.config)
    expected["rootfs"]["diff_ids"].append(layer["digest"])
    expected.setdefault("history", []).append(
        {"created_by": "ai-quant-os static single-decision worker assembly"}
    )
    expected["config"].update(
        User="65534:65534", WorkingDir="/scratch", Entrypoint=["/usr/bin/env"], Cmd=CMD
    )
    expected["config"].setdefault("Labels", {}).update(LABELS)
    if encode_metadata(config) != encode_metadata(expected):
        raise ValueError("Worker runtime/rootfs/history differs from fixed policy")
    expected_members = {
        "oci-layout",
        "index.json",
        "blobs/sha256/" + manifest_digest[7:],
        "blobs/sha256/" + config_digest[7:],
        "blobs/sha256/" + layer["digest"][7:],
    }
    expanded_total = 0
    for row, diff_id in zip(base.layers, base.config["rootfs"]["diff_ids"], strict=True):
        blob = _blob(members, row, "application/vnd.oci.image.layer.v1.tar+gzip", MAX_LAYER)
        expanded_total += check_base_blob(blob, row, diff_id)
        if expanded_total > MAX_EXPANDED:
            raise ValueError("Expanded worker base bundle exceeds bound")
        expected_members.add("blobs/sha256/" + row["digest"][7:])
    if set(members) != expected_members:
        raise ValueError("Worker OCI has unreferenced or missing members")
    return StrategyWorkerImageReceipt(
        state="ARCHIVE_HASHES_VERIFIED",
        base_manifest_digest=base_manifest_digest,
        worker_sha256=worker_sha256,
        source_pins=(ImageSourcePin(path="worker.py", sha256=worker_sha256), *base.source_pins),
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        worker_layer_digest=layer["digest"],
        archive_sha256=archive_sha256,
        archive_bytes=len(raw),
        archive_integrity_verified=True,
    )
