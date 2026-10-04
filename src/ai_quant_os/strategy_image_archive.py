"""Static OCI worker assembly: copy opaque pinned bytes, never run image/source code."""

from copy import deepcopy
from hashlib import sha256
import io
from pathlib import Path
import tarfile

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import (
    CMD,
    LABELS,
    MAX_ARCHIVE,
    MAX_EXPANDED,
    MAX_LAYER,
    check_base_blob,
    content_digest,
    encode_metadata,
    ordinary_directory,
    read_strategy_image_base,
    require_digest,
)
from ai_quant_os.strategy_image_contracts import ImageSourcePin, StrategyWorkerImageReceipt


def _worker_layer(worker: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in ("opt", "opt/strategy", "opt/strategy/worker.py", "scratch"):
            member = tarfile.TarInfo(name)
            member.uid = member.gid = member.mtime = 0
            if name.endswith("worker.py"):
                member.mode, member.size = 0o444, len(worker)
                archive.addfile(member, io.BytesIO(worker))
            else:
                member.type, member.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(member)
    return output.getvalue()


def _write_archive(output: Path, blobs: dict, manifest: bytes) -> tuple[str, int]:
    index = encode_metadata(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": content_digest(manifest),
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": "amd64"},
                    "annotations": {
                        "org.opencontainers.image.ref.name": "ai-quant-strategy:prepared"
                    },
                }
            ],
        }
    )
    members = {
        "oci-layout": encode_metadata({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        **{"blobs/sha256/" + key[7:]: raw for key, raw in blobs.items()},
    }
    pending = output / "image.oci.tar.part"
    with (
        pending.open("xb") as stream,
        tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive,
    ):
        for name, raw in sorted(members.items()):
            member = tarfile.TarInfo(name)
            member.mode, member.size, member.mtime = 0o644, len(raw), 0
            archive.addfile(member, io.BytesIO(raw))
    raw = read_repair_image_input(pending, MAX_ARCHIVE)
    pending.rename(output / "image.oci.tar")
    return sha256(raw).hexdigest(), len(raw)


def assemble_strategy_worker_image(
    worker: Path,
    base_metadata: Path,
    base_blobs: Path,
    output: Path,
    *,
    worker_sha256: str,
    base_manifest_digest: str,
) -> StrategyWorkerImageReceipt:
    require_digest(worker_sha256, prefix="")
    ordinary_directory(output.parent)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Worker image output must be new")
    source = read_repair_image_input(worker, 65536)
    if not source or sha256(source).hexdigest() != worker_sha256:
        raise ValueError("Worker differs from independent source pin")
    base = read_strategy_image_base(base_metadata, base_manifest_digest)
    base_blobs = ordinary_directory(base_blobs)
    blobs, expanded_total = {}, 0
    for row, diff_id in zip(base.layers, base.config["rootfs"]["diff_ids"], strict=True):
        raw = read_repair_image_input(base_blobs / row["digest"][7:], MAX_LAYER)
        expanded_total += check_base_blob(raw, row, diff_id)
        if expanded_total > MAX_EXPANDED:
            raise ValueError("Expanded worker base bundle exceeds bound")
        blobs[row["digest"]] = raw
    layer = _worker_layer(source)
    layer_digest = content_digest(layer)
    config = deepcopy(base.config)
    config["rootfs"]["diff_ids"].append(layer_digest)
    config.setdefault("history", []).append(
        {"created_by": "ai-quant-os static single-decision worker assembly"}
    )
    config["config"].update(
        User="65534:65534", WorkingDir="/scratch", Entrypoint=["/usr/bin/env"], Cmd=CMD
    )
    config["config"].setdefault("Labels", {}).update(LABELS)
    config_raw = encode_metadata(config)
    manifest = encode_metadata(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": content_digest(config_raw),
                "size": len(config_raw),
            },
            "layers": [
                *base.layers,
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": layer_digest,
                    "size": len(layer),
                },
            ],
        }
    )
    blobs.update(
        {
            layer_digest: layer,
            content_digest(config_raw): config_raw,
            content_digest(manifest): manifest,
        }
    )
    output.mkdir(mode=0o755)
    archive_sha256, archive_bytes = _write_archive(output, blobs, manifest)
    receipt = StrategyWorkerImageReceipt(
        base_manifest_digest=base_manifest_digest,
        worker_sha256=worker_sha256,
        source_pins=(ImageSourcePin(path="worker.py", sha256=worker_sha256), *base.source_pins),
        manifest_digest=content_digest(manifest),
        config_digest=content_digest(config_raw),
        worker_layer_digest=layer_digest,
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
    )
    with (output / "image_receipt.json").open("xb") as stream:
        stream.write(receipt.model_dump_json().encode() + b"\n")
    return receipt
