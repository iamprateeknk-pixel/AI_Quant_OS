"""Prepare bounded offline build inputs without a daemon or source execution."""

import json
import os
import re
import stat
from hashlib import sha256
from pathlib import Path


_METADATA_LIMIT = 1048576
_WHEEL_LIMIT = 16777216
_BUNDLE_LIMIT = 134217728


def _identity(item: os.stat_result) -> tuple:
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)


def _reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _directory(path: Path) -> Path:
    metadata = path.lstat()
    if _reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Build input root must be an ordinary directory")
    return path.resolve(strict=True)


def _read_file(path: Path, limit: int) -> bytes:
    before = path.lstat()
    if (
        _reparse(before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not 0 < before.st_size <= limit
    ):
        raise ValueError("Build input must be a bounded ordinary single-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (
        _identity(before) != _identity(opened)
        or _identity(before) != _identity(after)
        or _identity(before) != _identity(path.lstat())
        or len(raw) != before.st_size
    ):
        raise ValueError("Build input identity changed while reading")
    return raw


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate build metadata field")
        value[key] = item
    return value


def _object(raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=_unique_fields)
    if not isinstance(value, dict):
        raise ValueError("Build metadata must be an object")
    return value


def _digest(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def _pending(value: dict, state: str) -> None:
    if value.get("schema_version") != "1.0" or value.get("state") != state:
        raise ValueError("Unsupported build input state")
    if any(
        value.get(key) is not False
        for key in ("image_verified", "execution_verified", "promotion_allowed")
    ):
        raise ValueError("Input metadata cannot grant image or execution authority")


def _base_reference(inputs: dict[str, bytes]) -> str:
    pin = _object(inputs["base_image.json"])
    _pending(pin, "BASE_METADATA_ONLY")
    if (
        pin.get("repository") != "docker.io/library/python"
        or pin.get("platform") != "linux/amd64"
        or not isinstance(pin.get("python_version"), str)
        or not re.fullmatch(r"3\.11\.[0-9]+", pin["python_version"])
    ):
        raise ValueError("Unsupported worker base profile")
    for name in ("index", "manifest", "config"):
        if pin.get(name + "_digest") != _digest(inputs["base_" + name + ".json"]):
            raise ValueError("Worker base digest mismatch")
    index = _object(inputs["base_index.json"])
    manifest = _object(inputs["base_manifest.json"])
    config = _object(inputs["base_config.json"])
    if (
        not isinstance(manifest.get("config"), dict)
        or not isinstance(config.get("config"), dict)
        or not isinstance(config["config"].get("Env"), list)
    ):
        raise ValueError("Invalid base configuration shape")
    entries = index.get("manifests")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("Invalid base index")
    selected = [
        entry
        for entry in entries
        if entry.get("platform") == {"os": "linux", "architecture": "amd64"}
    ]
    if (
        len(selected) != 1
        or selected[0].get("digest") != pin["manifest_digest"]
        or manifest.get("config", {}).get("digest") != pin["config_digest"]
        or manifest.get("layers") != pin.get("layers")
        or config.get("architecture") != "amd64"
        or config.get("os") != "linux"
        or "PYTHON_VERSION=" + pin["python_version"] not in config.get("config", {}).get("Env", [])
    ):
        raise ValueError("Base platform/configuration binding mismatch")
    reference = pin["repository"] + "@" + pin["manifest_digest"]
    from_lines = [
        line.split()
        for line in inputs["Dockerfile"].decode().splitlines()
        if line.startswith("FROM ")
    ]
    if not from_lines or any(len(line) < 2 or line[1] != reference for line in from_lines):
        raise ValueError("Build recipe does not use the pinned base")
    return reference


def _locked_wheels(inputs: dict[str, bytes], wheels: Path) -> dict[str, bytes]:
    descriptor = _object(inputs["wheel_manifest.json"])
    _pending(descriptor, "WHEEL_INPUTS_ONLY")
    rows = descriptor.get("wheels")
    if (
        descriptor.get("target") != "cpython-3.11-linux-x86_64"
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 100
    ):
        raise ValueError("Unsupported wheel inventory")
    names, filenames, lock, payload = set(), set(), [], {}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid wheel entry")
        for key, pattern in (
            ("name", r"[a-z0-9]+(?:-[a-z0-9]+)*"),
            ("version", r"[A-Za-z0-9.!+_-]+"),
            ("filename", r"[A-Za-z0-9_.+-]+\.whl"),
            ("sha256", r"[a-f0-9]{64}"),
        ):
            if not isinstance(row.get(key), str) or not re.fullmatch(pattern, row[key]):
                raise ValueError("Invalid wheel inventory boundary")
        if row["name"] in names or row["filename"] in filenames:
            raise ValueError("Duplicate wheel inventory entry")
        names.add(row["name"])
        filenames.add(row["filename"])
        if (
            type(row.get("bytes")) is not int
            or not 0 < row["bytes"] <= _WHEEL_LIMIT
            or type(row.get("target_required")) is not bool
        ):
            raise ValueError("Invalid wheel size/selection")
        if not row["target_required"]:
            continue
        total += row["bytes"]
        if total > _BUNDLE_LIMIT:
            raise ValueError("Worker wheel bundle exceeds limit")
        raw = _read_file(wheels / row["filename"], _WHEEL_LIMIT)
        if len(raw) != row["bytes"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("Locked wheel bytes mismatch")
        payload["wheels/" + row["filename"]] = raw
        lock.append(row["name"] + "==" + row["version"] + " --hash=sha256:" + row["sha256"] + "\n")
    if not payload or inputs["requirements.lock"].decode().replace("\r\n", "\n") != "".join(lock):
        raise ValueError("Wheel inventory and requirements lock differ")
    return payload


def prepare_repair_image_context(recipes: Path, wheels: Path, destination: Path) -> dict:
    """Copy verified inputs into a new context; the recipe is trusted operator code.

    No archives are extracted/imported, no processes launched, and no image built.
    A write failure retains a partial directory; success receipt is written last.
    """
    recipes, wheels = _directory(recipes), _directory(wheels)
    parent = _directory(destination.parent)
    destination = parent / destination.name
    if any(
        destination.is_relative_to(root) or root.is_relative_to(destination)
        for root in (recipes, wheels)
    ):
        raise ValueError("Build destination overlaps input roots")
    if os.path.lexists(destination):
        raise FileExistsError("Build destination already exists; preserved")
    inputs = {
        name: _read_file(recipes / name, _METADATA_LIMIT)
        for name in (
            "base_image.json",
            "base_index.json",
            "base_manifest.json",
            "base_config.json",
            "wheel_manifest.json",
            "requirements.lock",
            "Dockerfile",
            ".dockerignore",
        )
    }
    reference = _base_reference(inputs)
    payload = _locked_wheels(inputs, wheels)
    wheel_count = len(payload)
    payload.update(
        {name: inputs[name] for name in ("Dockerfile", ".dockerignore", "requirements.lock")}
    )
    receipt = {
        "schema_version": "1.0",
        "state": "BUILD_CONTEXT_ONLY",
        "platform": "linux/amd64",
        "base_reference": reference,
        "wheel_count": wheel_count,
        "inputs": {name: sha256(raw).hexdigest() for name, raw in sorted(inputs.items())},
        "files": {
            name: {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
            for name, raw in sorted(payload.items())
        },
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
    }
    destination.mkdir(mode=0o755)
    (destination / "wheels").mkdir(mode=0o755)
    for directory in (destination, destination / "wheels"):
        directory.chmod(0o755)
    for name, raw in payload.items():
        with (destination / name).open("xb") as output:
            output.write(raw)
        (destination / name).chmod(0o644)
    with (destination / "context_receipt.json").open("xb") as output:
        output.write((json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
    (destination / "context_receipt.json").chmod(0o644)
    return receipt
