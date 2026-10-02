"""Image assembly uses wheel/base bytes as data and never executes their contents."""

import base64
import csv
import gzip
import importlib
import io
import json
import sys
import tarfile
import zipfile
from hashlib import sha256

import pytest

from test_repair_image_inputs import image_inputs as image_inputs, encode


def wheel_bytes(extra=None, *, corrupt_record=False):
    files = {
        "worker_fixture/__init__.py": b"raise RuntimeError('wheel must not be imported on host')\n",
        "example-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: example\nVersion: 1.0\n",
        "example-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "example-1.0.dist-info/entry_points.txt": b"[console_scripts]\nworker-fixture = worker_fixture:main\n",
    }
    files.update(extra or {})
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, raw in files.items():
        digest = base64.urlsafe_b64encode(sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + ("wrong" if corrupt_record else digest), str(len(raw))])
    writer.writerow(["example-1.0.dist-info/RECORD", "", ""])
    files["example-1.0.dist-info/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            entry = zipfile.ZipInfo(name)
            entry.filename = name
            archive.writestr(entry, raw)
    return output.getvalue()


def replace_wheel(image_inputs, raw):
    recipes, wheels, _, descriptor, row = image_inputs
    (wheels / row["filename"]).write_bytes(raw)
    row["bytes"], row["sha256"] = len(raw), sha256(raw).hexdigest()
    (recipes / "wheel_manifest.json").write_bytes(encode(descriptor))
    (recipes / "requirements.lock").write_bytes(
        ("example==1.0 --hash=sha256:" + row["sha256"] + "\n").encode()
    )


@pytest.fixture
def archive_inputs(image_inputs):
    recipes, _, output, _, _ = image_inputs
    blobs = output.parent / "base-blobs"
    blobs.mkdir()
    base_tar = io.BytesIO()
    with tarfile.open(fileobj=base_tar, mode="w") as archive:
        info = tarfile.TarInfo("base-marker.txt")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"base"))
    compressed = gzip.compress(base_tar.getvalue(), mtime=0)
    digest = "sha256:" + sha256(compressed).hexdigest()
    (blobs / digest[7:]).write_bytes(compressed)
    layers = [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": digest,
            "size": len(compressed),
        }
    ]
    config = json.loads((recipes / "base_config.json").read_bytes())
    config["rootfs"] = {
        "type": "layers",
        "diff_ids": ["sha256:" + sha256(base_tar.getvalue()).hexdigest()],
    }
    config["history"] = [{"created_by": "test base"}]
    config_raw = encode(config)
    manifest_raw = encode(
        {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + sha256(config_raw).hexdigest()},
            "layers": layers,
        }
    )
    manifest_digest = "sha256:" + sha256(manifest_raw).hexdigest()
    index_raw = encode(
        {
            "schemaVersion": 2,
            "manifests": [
                {"digest": manifest_digest, "platform": {"os": "linux", "architecture": "amd64"}}
            ],
        }
    )
    pin = json.loads((recipes / "base_image.json").read_bytes())
    pin.update(
        config_digest="sha256:" + sha256(config_raw).hexdigest(),
        manifest_digest=manifest_digest,
        index_digest="sha256:" + sha256(index_raw).hexdigest(),
        layers=layers,
    )
    for name, raw in (
        ("base_config.json", config_raw),
        ("base_manifest.json", manifest_raw),
        ("base_index.json", index_raw),
        ("base_image.json", encode(pin)),
    ):
        (recipes / name).write_bytes(raw)
    (recipes / "Dockerfile").write_bytes(
        ("FROM docker.io/library/python@" + manifest_digest + "\n").encode()
    )
    replace_wheel(image_inputs, wheel_bytes())
    return image_inputs, blobs, layers


def assemble(archive_inputs, output=None):
    assert importlib.util.find_spec("ai_quant_os.repair_image_archive"), (
        "Missing static OCI image assembly"
    )
    module = importlib.import_module("ai_quant_os.repair_image_archive")
    inputs, blobs, _ = archive_inputs
    recipes, wheels, destination, _, _ = inputs
    return module.assemble_repair_image(recipes, wheels, blobs, output or destination)


def read_image(output):
    with tarfile.open(output / "image.oci.tar") as archive:
        content = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    index = json.loads(content["index.json"])
    manifest = json.loads(content["blobs/sha256/" + index["manifests"][0]["digest"][7:]])
    config = json.loads(content["blobs/sha256/" + manifest["config"]["digest"][7:]])
    return content, manifest, config


def test_assembly_preserves_base_blobs_and_builds_linux_wheel_layer_without_execution(
    archive_inputs,
):
    receipt = assemble(archive_inputs)
    inputs, blobs, layers = archive_inputs
    output = inputs[2]
    content, manifest, config = read_image(output)
    assert json.loads(content["oci-layout"]) == {"imageLayoutVersion": "1.0.0"}
    assert manifest["layers"][:-1] == layers
    assert (
        content["blobs/sha256/" + layers[0]["digest"][7:]]
        == (blobs / layers[0]["digest"][7:]).read_bytes()
    )
    assert config["config"]["User"] == "65534:65534"
    assert config["config"]["WorkingDir"] == "/scratch"
    assert config["config"]["Entrypoint"] == ["/usr/bin/env"]
    assert config["config"]["Cmd"] == ["/usr/local/bin/python", "-I", "-B"]
    for name, raw in content.items():
        if name.startswith("blobs/sha256/"):
            assert sha256(raw).hexdigest() == name.split("/")[-1]
    layer = content["blobs/sha256/" + manifest["layers"][-1]["digest"][7:]]
    assert "sha256:" + sha256(layer).hexdigest() == config["rootfs"]["diff_ids"][-1]
    with tarfile.open(fileobj=io.BytesIO(layer)) as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        assert names == sorted(names), "Layer order must use POSIX names on every build host"
        assert all(item.uid == 0 and item.gid == 0 and item.mtime == 0 for item in members)
        module = archive.extractfile(
            "usr/local/lib/python3.11/site-packages/worker_fixture/__init__.py"
        ).read()
        assert module.startswith(b"raise RuntimeError")
        script = archive.extractfile("usr/local/bin/worker-fixture").read()
        assert script.startswith(b"#!/usr/local/bin/python\n")
        record = archive.extractfile(
            "usr/local/lib/python3.11/site-packages/example-1.0.dist-info/RECORD"
        ).read()
        assert b"\\" not in record and b"../../../bin/worker-fixture" in record
        assert not any(item.name.endswith(".pyc") for item in members)
        assert all(item.mode in (0o644, 0o755) for item in members)
    assert "worker_fixture" not in sys.modules
    assert receipt["state"] == "OCI_ARCHIVE_ONLY"
    assert all(
        receipt[key] is False
        for key in ("image_verified", "execution_verified", "promotion_allowed")
    )
    assert receipt["archive_sha256"] == sha256((output / "image.oci.tar").read_bytes()).hexdigest()
    assert json.loads((output / "image_receipt.json").read_bytes()) == receipt


def test_changed_base_blob_refuses_publishing_archive(archive_inputs):
    inputs, blobs, layers = archive_inputs
    (blobs / layers[0]["digest"][7:]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        assemble(archive_inputs)
    assert not (inputs[2] / "image.oci.tar").exists()
    assert not (inputs[2] / "image_receipt.json").exists()


@pytest.mark.parametrize(
    "path",
    ["../outside.py", "/outside.py", "C:/outside.py", "pkg\\outside.py", "pkg/../outside.py"],
)
def test_unsafe_wheel_members_refuse_before_any_installation(archive_inputs, path):
    inputs, _, _ = archive_inputs
    replace_wheel(inputs, wheel_bytes({path: b"must stay inert"}))
    with pytest.raises(ValueError):
        assemble(archive_inputs)
    assert not (inputs[2] / "image.oci.tar").exists()
    assert not (inputs[2].parent / "outside.py").exists()


def test_invalid_internal_record_refuses_archive(archive_inputs):
    inputs, _, _ = archive_inputs
    replace_wheel(inputs, wheel_bytes(corrupt_record=True))
    with pytest.raises(ValueError):
        assemble(archive_inputs)
    assert not (inputs[2] / "image.oci.tar").exists()


def test_existing_output_is_preserved(archive_inputs):
    output = archive_inputs[0][2]
    output.mkdir()
    (output / "keep").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        assemble(archive_inputs)
    assert list(output.iterdir()) == [output / "keep"]


def test_repeated_build_inputs_produce_identical_archives(archive_inputs):
    first = archive_inputs[0][2]
    second = first.with_name("second-image")
    a = assemble(archive_inputs)
    b = assemble(archive_inputs, second)
    assert a["archive_sha256"] == b["archive_sha256"]
    assert (first / "image.oci.tar").read_bytes() == (second / "image.oci.tar").read_bytes()


def test_empty_package_files_are_preserved_in_image_layer(archive_inputs):
    inputs, _, _ = archive_inputs
    replace_wheel(inputs, wheel_bytes({"worker_fixture/empty.py": b""}))
    assemble(archive_inputs)
    content, manifest, _ = read_image(inputs[2])
    layer = content["blobs/sha256/" + manifest["layers"][-1]["digest"][7:]]
    with tarfile.open(fileobj=io.BytesIO(layer)) as archive:
        assert (
            archive.extractfile(
                "usr/local/lib/python3.11/site-packages/worker_fixture/empty.py"
            ).read()
            == b""
        )
