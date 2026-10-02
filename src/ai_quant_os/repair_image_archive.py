"""Static OCI assembly of trusted dependency inputs; no daemon or target execution."""

import argparse
import csv
import gzip
import io
import json
import re
import stat
import tarfile
import zipfile
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from configparser import ConfigParser

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile

from ai_quant_os.repair_image_inputs import prepare_repair_image_context, read_repair_image_input


_MAX_LAYER = 67108864
_MAX_EXPANDED = 268435456
_SITE = "usr/local/lib/python3.11/site-packages"


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def _expanded_digest(raw: bytes) -> str:
    digest, count = sha256(), 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        while chunk := stream.read(1048576):
            count += len(chunk)
            if count > _MAX_EXPANDED:
                raise ValueError("Expanded base layer exceeds bound")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _base_blobs(recipes: Path, base_blobs: Path, context: dict) -> tuple[dict, list, dict]:
    inputs = {}
    for name in ("base_config.json", "base_manifest.json"):
        raw = read_repair_image_input(recipes / name, 1048576)
        if sha256(raw).hexdigest() != context["inputs"][name]:
            raise ValueError("Base metadata changed after context preparation")
        inputs[name] = json.loads(raw)
    config, manifest = inputs["base_config.json"], inputs["base_manifest.json"]
    rootfs, layers = config.get("rootfs"), manifest.get("layers")
    if (
        not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(layers, list)
        or not 1 <= len(layers) <= 16
        or not isinstance(rootfs.get("diff_ids"), list)
        or len(rootfs["diff_ids"]) != len(layers)
    ):
        raise ValueError("Unsupported base layer inventory")
    blobs, total = {}, 0
    for row, diff_id in zip(layers, rootfs["diff_ids"], strict=True):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("digest"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", row["digest"])
            or type(row.get("size")) is not int
            or not 0 < row["size"] <= _MAX_LAYER
            or row.get("mediaType") != "application/vnd.oci.image.layer.v1.tar+gzip"
        ):
            raise ValueError("Unsupported base layer descriptor")
        total += row["size"]
        if total > 134217728:
            raise ValueError("Compressed base bundle exceeds bound")
        raw = read_repair_image_input(base_blobs / row["digest"][7:], _MAX_LAYER)
        if (
            len(raw) != row["size"]
            or _digest(raw) != row["digest"]
            or _expanded_digest(raw) != diff_id
        ):
            raise ValueError("Base compressed/expanded layer hash mismatch")
        blobs[row["digest"]] = raw
    return config, layers, blobs


def _safe_member(name: str) -> None:
    parts = name.rstrip("/").split("/")
    if (
        not name
        or len(name) > 240
        or any(part in ("", ".", "..") for part in parts)
        or "\\" in name
        or any(char in name for char in ':*?"<>|\x00')
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("Unsafe wheel archive path")
    for part in parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *[f"COM{i}" for i in range(1, 10)],
            *[f"LPT{i}" for i in range(1, 10)],
        }:
            raise ValueError("Unsupported host wheel path")


def _preflight_wheels(context: Path, receipt: dict) -> list[Path]:
    wheels, total, count = [], 0, 0
    for name, row in sorted(receipt["files"].items()):
        if not name.startswith("wheels/"):
            continue
        wheel = context / name
        raw = read_repair_image_input(wheel, 16777216)
        if len(raw) != row["bytes"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("Prepared wheel changed")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            seen = set()
            for item in archive.infolist():
                _safe_member(item.orig_filename)
                if (
                    item.filename.casefold() in seen
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.flag_bits & 1
                ):
                    raise ValueError("Ambiguous wheel member")
                seen.add(item.filename.casefold())
                count += 1
                total += item.file_size
                if count > 10000 or total > _MAX_EXPANDED or item.file_size > 16777216:
                    raise ValueError("Expanded wheel inventory exceeds bound")
        with WheelFile.open(wheel) as source:
            source.validate_record()
            if source.data_dir:
                with zipfile.ZipFile(wheel) as archive:
                    if any(
                        name.startswith(source.data_dir) and name.split("/")[0] != source.data_dir
                        for name in archive.namelist()
                    ):
                        raise ValueError("Ambiguous wheel data directory")
            try:
                entries = source.read_dist_info("entry_points.txt")
            except KeyError:
                entries = None
            if entries:
                # Entry point keys become generated filenames, independently of ZIP paths.
                parser = ConfigParser(interpolation=None)
                parser.read_string(entries)
                for section in ("console_scripts", "gui_scripts"):
                    if parser.has_section(section):
                        for script in parser[section]:
                            _safe_member(script)
                            if "/" in script:
                                raise ValueError("Unsafe wheel launcher name")
        wheels.append(wheel)
    return wheels


def _wheel_layer(wheels: list[Path], staging: Path) -> bytes:
    scheme = {
        "purelib": "/" + _SITE,
        "platlib": "/" + _SITE,
        "scripts": "/usr/local/bin",
        "headers": "/usr/local/include/python3.11",
        "data": "/usr/local",
    }
    destination = SchemeDictionaryDestination(
        scheme,
        interpreter="/usr/local/bin/python",
        script_kind="posix",
        bytecode_optimization_levels=(),
        destdir=str(staging),
    )
    for wheel in wheels:
        with WheelFile.open(wheel) as source:
            install(
                source,
                destination,
                additional_metadata={
                    "INSTALLER": b"ai-quant-os static assembler; installer 0.7.0\n"
                },
            )
    # installer 0.7 uses host relpath for cross-scheme RECORD entries. Linux uses '/'.
    for path in staging.glob(_SITE + "/*.dist-info/RECORD"):
        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for row in rows:
            row[0] = row[0].replace("\\", "/")
            writer.writerow(row)
        path.write_bytes(output.getvalue().encode())
    output = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(
            staging.rglob("*"), key=lambda item: item.relative_to(staging).as_posix()
        ):
            metadata = path.lstat()
            name = path.relative_to(staging).as_posix()
            info = tarfile.TarInfo(name)
            info.uid, info.gid, info.mtime = 0, 0, 0
            if stat.S_ISDIR(metadata.st_mode):
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            else:
                raw = read_repair_image_input(path, 16777216)
                total += len(raw)
                if total > _MAX_EXPANDED:
                    raise ValueError("Installed wheel files exceed bound")
                info.size = len(raw)
                info.mode = 0o755 if name.startswith("usr/local/bin/") else 0o644
                archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def _archive(output: Path, blobs: dict[str, bytes], manifest: bytes) -> str:
    index = _encode(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": _digest(manifest),
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": "amd64"},
                    "annotations": {
                        "org.opencontainers.image.ref.name": "ai-quant-repair:inputs-only"
                    },
                }
            ],
        }
    )
    members = {
        "oci-layout": _encode({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        **{"blobs/sha256/" + digest[7:]: raw for digest, raw in blobs.items()},
    }
    pending = output / "image.oci.tar.part"
    with (
        pending.open("xb") as stream,
        tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for name, raw in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            archive.addfile(info, io.BytesIO(raw))
    digest = sha256(pending.read_bytes()).hexdigest()
    pending.rename(output / "image.oci.tar")
    return digest


def assemble_repair_image(recipes: Path, wheels: Path, base_blobs: Path, output: Path) -> dict:
    """Assemble a local OCI archive; all image/execution admission remains pending."""
    if version("installer") != "0.7.0":
        raise ValueError("Static image build requires pinned installer 0.7.0")
    output.mkdir(mode=0o755)
    context = output / "context"
    inputs = prepare_repair_image_context(recipes, wheels, context)
    config, layers, blobs = _base_blobs(recipes, base_blobs, inputs)
    selected = _preflight_wheels(context, inputs)
    layer = _wheel_layer(selected, output / "installed-root")
    layer_digest = _digest(layer)
    config["rootfs"]["diff_ids"].append(layer_digest)
    config.setdefault("history", []).append(
        {"created_by": "ai-quant-os static dependency assembly; installer 0.7.0"}
    )
    runtime = config["config"]
    runtime.update(
        User="65534:65534",
        WorkingDir="/scratch",
        Entrypoint=["/usr/bin/env"],
        Cmd=["/usr/local/bin/python", "-I", "-B"],
    )
    runtime["Env"] = [
        entry for entry in runtime["Env"] if not entry.startswith("PYTHONDONTWRITEBYTECODE=")
    ] + ["PYTHONDONTWRITEBYTECODE=1"]
    runtime.setdefault("Labels", {}).update(
        {
            "ai.quant.repair.state": "OCI_ARCHIVE_ONLY",
            "ai.quant.repair.execution-verified": "false",
            "ai.quant.repair.promotion-allowed": "false",
        }
    )
    config_bytes = _encode(config)
    manifest = _encode(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": _digest(config_bytes),
                "size": len(config_bytes),
            },
            "layers": [
                *layers,
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": layer_digest,
                    "size": len(layer),
                },
            ],
        }
    )
    blobs.update(
        {layer_digest: layer, _digest(config_bytes): config_bytes, _digest(manifest): manifest}
    )
    archive_sha256 = _archive(output, blobs, manifest)
    receipt = {
        "schema_version": "1.0",
        "state": "OCI_ARCHIVE_ONLY",
        "base_reference": inputs["base_reference"],
        "wheel_count": inputs["wheel_count"],
        "inputs": inputs["inputs"],
        "manifest_digest": _digest(manifest),
        "config_digest": _digest(config_bytes),
        "wheel_layer_digest": layer_digest,
        "archive_sha256": archive_sha256,
        "archive_bytes": (output / "image.oci.tar").stat().st_size,
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
    }
    with (output / "image_receipt.json").open("xb") as stream:
        stream.write(_encode(receipt) + b"\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble dependency-only OCI archive; execute nothing"
    )
    for name in ("recipes", "wheels", "base-blobs", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = assemble_repair_image(args.recipes, args.wheels, args.base_blobs, args.output)
    except (OSError, ValueError, zipfile.BadZipFile):
        parser.exit(2, "Image inputs rejected; inspect and retain any partial output.\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
