"""Build input boundaries; these tests never execute image or candidate code."""

import importlib
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


@pytest.fixture
def image_inputs(tmp_path):
    recipes = tmp_path / "recipes"
    wheels = tmp_path / "wheels"
    recipes.mkdir()
    wheels.mkdir()
    config = encode(
        {"architecture": "amd64", "os": "linux", "config": {"Env": ["PYTHON_VERSION=3.11.17"]}}
    )
    manifest = encode(
        {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + sha256(config).hexdigest()},
            "layers": [],
        }
    )
    digest = "sha256:" + sha256(manifest).hexdigest()
    index = encode(
        {
            "schemaVersion": 2,
            "manifests": [{"digest": digest, "platform": {"os": "linux", "architecture": "amd64"}}],
        }
    )
    pin = {
        "schema_version": "1.0",
        "state": "BASE_METADATA_ONLY",
        "repository": "docker.io/library/python",
        "tag": "3.11.17-slim-bookworm",
        "platform": "linux/amd64",
        "python_version": "3.11.17",
        "index_digest": "sha256:" + sha256(index).hexdigest(),
        "manifest_digest": digest,
        "config_digest": "sha256:" + sha256(config).hexdigest(),
        "layers": [],
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
    }
    for name, raw in (
        ("base_index.json", index),
        ("base_manifest.json", manifest),
        ("base_config.json", config),
    ):
        (recipes / name).write_bytes(raw)
    (recipes / "base_image.json").write_bytes(encode(pin))
    (recipes / "Dockerfile").write_text(
        "FROM docker.io/library/python@" + digest + "\n", encoding="utf-8"
    )
    (recipes / ".dockerignore").write_text(
        "**\n!Dockerfile\n!requirements.lock\n!wheels/\n!wheels/*.whl\n", encoding="utf-8"
    )
    content = b"opaque wheel bytes, never imported"
    filename = "example-1.0-py3-none-any.whl"
    row = {
        "name": "example",
        "version": "1.0",
        "filename": filename,
        "bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "target_required": True,
    }
    excluded = {
        **row,
        "name": "unused",
        "filename": "unused-1.0-py3-none-any.whl",
        "target_required": False,
    }
    descriptor = {
        "schema_version": "1.0",
        "state": "WHEEL_INPUTS_ONLY",
        "target": "cpython-3.11-linux-x86_64",
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
        "wheels": [row, excluded],
    }
    (recipes / "wheel_manifest.json").write_bytes(encode(descriptor))
    (recipes / "requirements.lock").write_text(
        "example==1.0 --hash=sha256:" + row["sha256"] + "\n", encoding="utf-8"
    )
    (wheels / filename).write_bytes(content)
    (wheels / excluded["filename"]).write_bytes(content)
    return recipes, wheels, tmp_path / "context", descriptor, row


def prepare(recipes, wheels, destination):
    assert importlib.util.find_spec("ai_quant_os.repair_image_inputs"), (
        "Missing offline image context preparation"
    )
    module = importlib.import_module("ai_quant_os.repair_image_inputs")
    return module.prepare_repair_image_context(recipes, wheels, destination)


def test_context_copies_only_pinned_inputs_and_issues_no_image_authority(image_inputs):
    recipes, wheels, destination, _, row = image_inputs
    (recipes / ".env").write_text("fixture private file")
    (wheels / "candidate.py").write_text("raise RuntimeError('must never execute')")
    receipt = prepare(recipes, wheels, destination)
    assert {
        p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()
    } == {
        "Dockerfile",
        ".dockerignore",
        "requirements.lock",
        "wheels/" + row["filename"],
        "context_receipt.json",
    }
    assert (destination / "wheels" / row["filename"]).read_bytes() == (
        wheels / row["filename"]
    ).read_bytes()
    assert receipt["state"] == "BUILD_CONTEXT_ONLY"
    assert receipt["wheel_count"] == 1
    assert receipt["image_verified"] is False
    assert receipt["execution_verified"] is False
    assert receipt["promotion_allowed"] is False
    assert "image_id" not in receipt
    assert json.loads((destination / "context_receipt.json").read_bytes()) == receipt
    assert receipt["files"]["wheels/" + row["filename"]]["sha256"] == row["sha256"]


@pytest.mark.parametrize("damage", ["bytes", "lock", "manifest", "config", "index", "recipe"])
def test_changed_pins_or_inputs_refuse_before_creating_output(image_inputs, damage):
    recipes, wheels, destination, _, row = image_inputs
    targets = {
        "bytes": wheels / row["filename"],
        "lock": recipes / "requirements.lock",
        "manifest": recipes / "base_manifest.json",
        "config": recipes / "base_config.json",
        "index": recipes / "base_index.json",
        "recipe": recipes / "Dockerfile",
    }
    targets[damage].write_bytes(b"changed")
    with pytest.raises(ValueError):
        prepare(recipes, wheels, destination)
    assert not destination.exists()


@pytest.mark.parametrize("damage", ["path", "duplicate", "size", "authority", "target"])
def test_manifest_boundary_refuses_unsafe_inventory(image_inputs, damage):
    recipes, wheels, destination, descriptor, row = image_inputs
    if damage == "path":
        row["filename"] = "../outside.whl"
    elif damage == "duplicate":
        descriptor["wheels"].append(dict(row))
    elif damage == "size":
        row["bytes"] = True
    elif damage == "authority":
        descriptor["image_verified"] = True
    else:
        descriptor["target"] = "windows"
    (recipes / "wheel_manifest.json").write_bytes(encode(descriptor))
    with pytest.raises(ValueError):
        prepare(recipes, wheels, destination)
    assert not destination.exists()


def test_existing_destination_is_preserved(image_inputs):
    recipes, wheels, destination, _, _ = image_inputs
    destination.mkdir()
    (destination / "keep.txt").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        prepare(recipes, wheels, destination)
    assert (destination / "keep.txt").read_bytes() == b"keep"
    assert len(list(destination.iterdir())) == 1


def test_hard_linked_wheel_is_refused(image_inputs):
    recipes, wheels, destination, _, row = image_inputs
    os.link(wheels / row["filename"], wheels / "linked.whl")
    with pytest.raises(ValueError):
        prepare(recipes, wheels, destination)
    assert not destination.exists()


def test_destination_inside_input_directory_is_refused(image_inputs):
    recipes, wheels, _, _, _ = image_inputs
    with pytest.raises(ValueError):
        prepare(recipes, wheels, wheels / "context")
    assert not (wheels / "context").exists()


def test_cli_prepares_context_without_loading_project_or_candidate_source(image_inputs):
    recipes, wheels, destination, _, _ = image_inputs
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_repair_image.py"
    assert script.is_file(), "Missing build-input CLI"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--recipes",
            str(recipes),
            "--wheels",
            str(wheels),
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "BUILD_CONTEXT_ONLY"


@pytest.mark.parametrize("field", ["config", "manifest"])
def test_malformed_base_shape_is_rejected_as_an_input_error(image_inputs, field):
    recipes, wheels, destination, _, _ = image_inputs
    name = "base_" + field + ".json"
    value = json.loads((recipes / name).read_bytes())
    value["config"] = []
    raw = encode(value)
    (recipes / name).write_bytes(raw)
    pin = json.loads((recipes / "base_image.json").read_bytes())
    pin[field + "_digest"] = "sha256:" + sha256(raw).hexdigest()
    if field == "config":
        manifest = json.loads((recipes / "base_manifest.json").read_bytes())
        manifest["config"]["digest"] = pin["config_digest"]
        manifest_raw = encode(manifest)
        (recipes / "base_manifest.json").write_bytes(manifest_raw)
        pin["manifest_digest"] = "sha256:" + sha256(manifest_raw).hexdigest()
    index = json.loads((recipes / "base_index.json").read_bytes())
    index["manifests"][0]["digest"] = pin["manifest_digest"]
    index_raw = encode(index)
    (recipes / "base_index.json").write_bytes(index_raw)
    pin["index_digest"] = "sha256:" + sha256(index_raw).hexdigest()
    (recipes / "base_image.json").write_bytes(encode(pin))
    with pytest.raises(ValueError):
        prepare(recipes, wheels, destination)
    assert not destination.exists()


def test_duplicate_json_fields_are_refused(image_inputs):
    recipes, wheels, destination, _, _ = image_inputs
    raw = (recipes / "wheel_manifest.json").read_bytes()
    (recipes / "wheel_manifest.json").write_bytes(raw.replace(b"{", b'{"image_verified": true,', 1))
    with pytest.raises(ValueError):
        prepare(recipes, wheels, destination)
    assert not destination.exists()


def test_interrupted_copy_retains_partial_context_without_success_receipt(
    image_inputs, monkeypatch
):
    recipes, wheels, destination, _, row = image_inputs
    original = Path.open

    def interrupted(path, mode="r", *args, **kwargs):
        if path == destination / "Dockerfile" and mode == "xb":
            raise OSError("simulated interrupted context copy")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", interrupted)
    with pytest.raises(OSError):
        prepare(recipes, wheels, destination)
    assert (destination / "wheels" / row["filename"]).is_file()
    assert not (destination / "context_receipt.json").exists()


def test_git_checkout_preserves_registry_bytes_with_windows_line_endings(tmp_path):
    project = Path(__file__).resolve().parents[1]
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".gitattributes").write_bytes((project / ".gitattributes").read_bytes())
    target = repository / "infra/repair-worker/base_manifest.json"
    target.parent.mkdir(parents=True)
    raw = (project / "infra/repair-worker/base_manifest.json").read_bytes()
    target.write_bytes(raw)
    for args in (("init",), ("config", "core.autocrlf", "true"), ("add", ".")):
        result = subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
    target.unlink()
    result = subprocess.run(
        ["git", "-C", str(repository), "checkout-index", "--all"], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == raw, "Registry digest changed during checkout"
