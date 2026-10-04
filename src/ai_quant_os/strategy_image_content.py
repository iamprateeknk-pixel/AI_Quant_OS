"""Bounded OCI content primitives shared by static assembly and parent inspection."""

from dataclasses import dataclass
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import stat

from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_contracts import ImageSourcePin

MAX_ARCHIVE = 134217728
MAX_LAYER = 67108864
MAX_EXPANDED = 268435456
BASE_NAMES = ("base_image.json", "base_index.json", "base_manifest.json", "base_config.json")
CMD = [
    "-i",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "HOME=/scratch/home",
    "TMPDIR=/scratch",
    "/usr/local/bin/python",
    "-I",
    "-B",
    "/opt/strategy/worker.py",
]
LABELS = {
    "ai.quant.strategy.state": "OCI_ARCHIVE_ONLY",
    "ai.quant.strategy.execution-verified": "false",
    "ai.quant.strategy.promotion-allowed": "false",
}


@dataclass(frozen=True)
class BaseImage:
    config: dict
    layers: tuple[dict, ...]
    source_pins: tuple[ImageSourcePin, ...]


def encode_metadata(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def content_digest(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def require_digest(value: str, *, prefix: str = "sha256:") -> None:
    if type(value) is not str or not re.fullmatch(re.escape(prefix) + "[a-f0-9]{64}", value):
        raise ValueError("Independent worker image digest required")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate worker image metadata field")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("Non-finite worker image metadata")


def parse_metadata(raw: bytes) -> dict:
    if len(raw) > 1048576:
        raise ValueError("Worker image metadata exceeds bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_no_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid worker image JSON") from error
    if type(value) is not dict:
        raise ValueError("Worker image metadata must be an object")
    return value


def ordinary_directory(path: Path) -> Path:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    ):
        raise ValueError("Worker image root must be an ordinary directory")
    return path.resolve(strict=True)


def require_descriptor(value: dict, media_type: str, maximum: int) -> None:
    if (
        type(value) is not dict
        or value.get("mediaType") != media_type
        or type(value.get("size")) is not int
        or not 0 < value["size"] <= maximum
    ):
        raise ValueError("Invalid worker image descriptor")
    require_digest(value.get("digest"))


def read_strategy_image_base(metadata: Path, base_manifest_digest: str) -> BaseImage:
    require_digest(base_manifest_digest)
    metadata = ordinary_directory(metadata)
    raw = {name: read_repair_image_input(metadata / name, 1048576) for name in BASE_NAMES}
    pin, index, manifest, config = (parse_metadata(raw[name]) for name in BASE_NAMES)
    if (
        pin.get("schema_version") != "1.0"
        or pin.get("state") != "BASE_METADATA_ONLY"
        or pin.get("repository") != "docker.io/library/python"
        or pin.get("platform") != "linux/amd64"
        or pin.get("python_version") != "3.11.17"
        or any(
            pin.get(name) is not False
            for name in ("image_verified", "execution_verified", "promotion_allowed")
        )
    ):
        raise ValueError("Unsupported worker base profile")
    if (
        pin.get("manifest_digest") != base_manifest_digest
        or content_digest(raw["base_manifest.json"]) != base_manifest_digest
    ):
        raise ValueError("Worker base differs from independent pin")
    for kind in ("index", "config"):
        if pin.get(kind + "_digest") != content_digest(raw["base_" + kind + ".json"]):
            raise ValueError("Worker base metadata hash mismatch")
    descriptor = manifest.get("config")
    require_descriptor(descriptor, "application/vnd.oci.image.config.v1+json", 1048576)
    if descriptor["digest"] != pin["config_digest"] or descriptor["size"] != len(
        raw["base_config.json"]
    ):
        raise ValueError("Worker base config descriptor mismatch")
    if type(index.get("manifests")) is not list:
        raise ValueError("Worker base manifest inventory must be a list")
    selected = [
        row
        for row in index.get("manifests", [])
        if type(row) is dict
        and row.get("digest") == base_manifest_digest
        and row.get("platform") == {"os": "linux", "architecture": "amd64"}
    ]
    if index.get("schemaVersion") != 2 or len(selected) != 1 or manifest.get("schemaVersion") != 2:
        raise ValueError("Worker base platform selection mismatch")
    require_descriptor(selected[0], "application/vnd.oci.image.manifest.v1+json", 1048576)
    if selected[0]["size"] != len(raw["base_manifest.json"]):
        raise ValueError("Worker base manifest descriptor mismatch")
    layers = manifest.get("layers")
    rootfs = config.get("rootfs")
    runtime = config.get("config")
    history = config.get("history", [])
    if (
        config.get("architecture") != "amd64"
        or config.get("os") != "linux"
        or type(runtime) is not dict
        or type(runtime.get("Env")) is not list
        or set(runtime) != {"Env", "Cmd"}
        or runtime.get("Cmd") != ["python3"]
        or runtime["Env"].count("PYTHON_VERSION=3.11.17") != 1
        or type(layers) is not list
        or not 1 <= len(layers) <= 16
        or pin.get("layers") != layers
        or type(rootfs) is not dict
        or rootfs.get("type") != "layers"
        or type(rootfs.get("diff_ids")) is not list
        or len(rootfs["diff_ids"]) != len(layers)
    ):
        raise ValueError("Worker base runtime/layer inventory mismatch")
    environment = runtime["Env"]
    if any(
        type(entry) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", entry)
        for entry in environment
    ) or len({entry.split("=", 1)[0] for entry in environment}) != len(environment):
        raise ValueError("Worker base environment must have unique named strings")
    if (
        type(history) is not list
        or any(
            type(row) is not dict or ("empty_layer" in row and type(row["empty_layer"]) is not bool)
            for row in history
        )
        or sum(not row.get("empty_layer", False) for row in history) != len(layers)
    ):
        raise ValueError("Worker base history must match its layer inventory")
    total = 0
    for layer, expanded in zip(layers, rootfs["diff_ids"], strict=True):
        require_descriptor(layer, "application/vnd.oci.image.layer.v1.tar+gzip", MAX_LAYER)
        require_digest(expanded)
        total += layer["size"]
    if total > MAX_ARCHIVE or len({row["digest"] for row in layers}) != len(layers):
        raise ValueError("Worker base bundle bound/uniqueness mismatch")
    return BaseImage(
        config,
        tuple(layers),
        tuple(
            ImageSourcePin(path=name, sha256=sha256(raw[name]).hexdigest()) for name in BASE_NAMES
        ),
    )


def expanded_layer_digest(raw: bytes) -> tuple[str, int]:
    digest, count = sha256(), 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        while chunk := stream.read(1048576):
            count += len(chunk)
            if count > MAX_EXPANDED:
                raise ValueError("Expanded worker base exceeds bound")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), count


def check_base_blob(raw: bytes, descriptor: dict, diff_id: str) -> int:
    if len(raw) != descriptor["size"] or content_digest(raw) != descriptor["digest"]:
        raise ValueError("Worker base compressed hash/size mismatch")
    expanded, count = expanded_layer_digest(raw)
    if expanded != diff_id:
        raise ValueError("Worker base expanded hash mismatch")
    return count
