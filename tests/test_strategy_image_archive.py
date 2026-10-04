import gzip
from hashlib import sha256
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return "sha256:" + sha256(raw).hexdigest()


def api():
    assert importlib.util.find_spec("ai_quant_os.strategy_image_archive"), (
        "Strategy worker image assembly is required"
    )
    from ai_quant_os.strategy_image_archive import assemble_strategy_worker_image
    from ai_quant_os.strategy_image_verification import verify_strategy_worker_image

    return assemble_strategy_worker_image, verify_strategy_worker_image


@pytest.fixture
def inputs(tmp_path):
    metadata = tmp_path / "base"
    blobs = tmp_path / "blobs"
    metadata.mkdir()
    blobs.mkdir()
    expanded = b"inert base bytes; not extracted or executed" * 10
    compressed = gzip.compress(expanded, mtime=0)
    descriptor = {
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
        "digest": digest(compressed),
        "size": len(compressed),
    }
    (blobs / descriptor["digest"][7:]).write_bytes(compressed)
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"Env": ["PYTHON_VERSION=3.11.17"], "Cmd": ["python3"]},
        "history": [{"created_by": "inert fixture"}],
        "rootfs": {"type": "layers", "diff_ids": [digest(expanded)]},
    }
    config_raw = encode(config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": digest(config_raw),
            "size": len(config_raw),
        },
        "layers": [descriptor],
    }
    manifest_raw = encode(manifest)
    index_raw = encode(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": digest(manifest_raw),
                    "size": len(manifest_raw),
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
    )
    pin = {
        "schema_version": "1.0",
        "state": "BASE_METADATA_ONLY",
        "repository": "docker.io/library/python",
        "platform": "linux/amd64",
        "python_version": "3.11.17",
        "index_digest": digest(index_raw),
        "manifest_digest": digest(manifest_raw),
        "config_digest": digest(config_raw),
        "layers": [descriptor],
        "image_verified": False,
        "execution_verified": False,
        "promotion_allowed": False,
    }
    for name, raw in [
        ("base_image.json", encode(pin)),
        ("base_index.json", index_raw),
        ("base_manifest.json", manifest_raw),
        ("base_config.json", config_raw),
    ]:
        (metadata / name).write_bytes(raw)
    marker = tmp_path / "must-not-execute.txt"
    worker = tmp_path / "worker.py"
    worker.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    return {
        "worker": worker,
        "worker_sha256": sha256(worker.read_bytes()).hexdigest(),
        "base_metadata": metadata,
        "base_blobs": blobs,
        "base_manifest_digest": digest(manifest_raw),
        "output": tmp_path / "image",
    }, marker


def build(inputs):
    assemble, _ = api()
    return assemble(**inputs[0])


def check(inputs, receipt, *, archive_sha256=None):
    _, verify = api()
    arguments, _ = inputs
    return verify(
        arguments["output"] / "image.oci.tar",
        base_metadata=arguments["base_metadata"],
        base_manifest_digest=arguments["base_manifest_digest"],
        worker_sha256=arguments["worker_sha256"],
        archive_sha256=archive_sha256 or receipt.archive_sha256,
        manifest_digest=receipt.manifest_digest,
        config_digest=receipt.config_digest,
    )


def members(path):
    with tarfile.open(path, mode="r:") as archive:
        return {row.name: archive.extractfile(row).read() for row in archive}


def test_repeatable_archive_contains_only_pinned_base_and_exact_opaque_worker(inputs):
    first = build(inputs)
    arguments, marker = inputs
    second = api()[0](**{**arguments, "output": arguments["output"].with_name("second")})
    assert first == second
    archive = arguments["output"] / "image.oci.tar"
    assert (
        archive.read_bytes()
        == (arguments["output"].with_name("second") / "image.oci.tar").read_bytes()
    )
    assert not marker.exists()
    assert first.state == "OCI_ARCHIVE_ONLY" and not first.archive_integrity_verified
    assert not first.image_verified and not first.execution_verified and not first.promotion_allowed
    content = members(archive)
    manifest = json.loads(content["blobs/sha256/" + first.manifest_digest[7:]])
    config = json.loads(content["blobs/sha256/" + first.config_digest[7:]])
    assert len(manifest["layers"]) == 2
    assert config["config"]["User"] == "65534:65534"
    assert config["config"]["Cmd"][-1] == "/opt/strategy/worker.py"
    assert config["config"]["Cmd"][:4] == [
        "-i",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "HOME=/scratch/home",
        "TMPDIR=/scratch",
    ]
    with tarfile.open(
        fileobj=io.BytesIO(content["blobs/sha256/" + first.worker_layer_digest[7:]])
    ) as layer:
        assert [row.name for row in layer] == [
            "opt",
            "opt/strategy",
            "opt/strategy/worker.py",
            "scratch",
        ]
        row = layer.getmember("opt/strategy/worker.py")
        assert row.mode == 0o444 and row.uid == row.gid == row.mtime == 0
        assert layer.extractfile(row).read() == arguments["worker"].read_bytes()


def test_readonly_verification_binds_independent_pins_without_execution_authority(inputs):
    receipt = build(inputs)
    path = inputs[0]["output"] / "image.oci.tar"
    before = path.read_bytes()
    checked = check(inputs, receipt)
    assert checked.state == "ARCHIVE_HASHES_VERIFIED" and checked.archive_integrity_verified
    assert checked.worker_sha256 == inputs[0]["worker_sha256"]
    assert checked.manifest_digest == receipt.manifest_digest
    assert checked.archive_sha256 == receipt.archive_sha256
    assert (
        not checked.image_verified
        and not checked.execution_verified
        and not checked.promotion_allowed
    )
    assert path.read_bytes() == before and not inputs[1].exists()


@pytest.mark.parametrize(
    "change",
    [
        "worker_pin",
        "base_pin",
        "blob",
        "base_config",
        "authority",
        "worker_hardlink",
        "existing_output",
    ],
)
def test_invalid_inputs_are_refused_without_reusing_or_healing_output(inputs, change):
    arguments, _ = inputs
    if change == "worker_pin":
        arguments["worker_sha256"] = "0" * 64
    elif change == "base_pin":
        arguments["base_manifest_digest"] = "sha256:" + "0" * 64
    elif change == "blob":
        next(arguments["base_blobs"].iterdir()).write_bytes(b"changed")
    elif change == "base_config":
        (arguments["base_metadata"] / "base_config.json").write_bytes(b"{}")
    elif change == "authority":
        path = arguments["base_metadata"] / "base_image.json"
        pin = json.loads(path.read_bytes())
        pin["image_verified"] = True
        path.write_bytes(encode(pin))
    elif change == "worker_hardlink":
        os.link(arguments["worker"], arguments["worker"].with_name("alias"))
    else:
        arguments["output"].mkdir()
        (arguments["output"] / "sentinel").write_bytes(b"preserve")
    with pytest.raises((ValueError, FileExistsError)):
        build(inputs)
    if change == "existing_output":
        assert (arguments["output"] / "sentinel").read_bytes() == b"preserve"
    else:
        assert not arguments["output"].exists()


@pytest.mark.parametrize(
    "pin",
    ["worker_sha256", "base_manifest_digest", "archive_sha256", "manifest_digest", "config_digest"],
)
def test_verifier_requires_each_independent_pin(inputs, pin):
    receipt = build(inputs)
    arguments, _ = inputs
    keyword = {
        "base_metadata": arguments["base_metadata"],
        "base_manifest_digest": arguments["base_manifest_digest"],
        "worker_sha256": arguments["worker_sha256"],
        "archive_sha256": receipt.archive_sha256,
        "manifest_digest": receipt.manifest_digest,
        "config_digest": receipt.config_digest,
    }
    keyword[pin] = ("" if pin.endswith("sha256") else "sha256:") + "0" * 64
    with pytest.raises(ValueError):
        api()[1](arguments["output"] / "image.oci.tar", **keyword)


def test_rehashed_archive_with_added_unreferenced_member_is_refused(inputs):
    receipt = build(inputs)
    path = inputs[0]["output"] / "image.oci.tar"
    content = members(path)
    content["untrusted.txt"] = b"not part of this image"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in content.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    path.write_bytes(stream.getvalue())
    with pytest.raises(ValueError):
        check(inputs, receipt, archive_sha256=sha256(path.read_bytes()).hexdigest())


def test_actual_assemble_and_verify_cli_are_data_only(inputs):
    arguments, marker = inputs
    common = [
        "--base-metadata",
        str(arguments["base_metadata"]),
        "--base-manifest-digest",
        arguments["base_manifest_digest"],
        "--worker-sha256",
        arguments["worker_sha256"],
    ]
    command = [sys.executable, "-m", "ai_quant_os.strategy_image_cli"]
    result = subprocess.run(
        [
            *command,
            "assemble",
            *common,
            "--worker",
            str(arguments["worker"]),
            "--base-blobs",
            str(arguments["base_blobs"]),
            "--output",
            str(arguments["output"]),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    result = subprocess.run(
        [
            *command,
            "verify",
            *common,
            "--archive",
            str(arguments["output"] / "image.oci.tar"),
            "--archive-sha256",
            receipt["archive_sha256"],
            "--manifest-digest",
            receipt["manifest_digest"],
            "--config-digest",
            receipt["config_digest"],
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["archive_integrity_verified"] is True
    assert not marker.exists()


def rewrite_image(inputs, receipt, mutate):
    path = inputs[0]["output"] / "image.oci.tar"
    content = members(path)
    manifest_name = "blobs/sha256/" + receipt.manifest_digest[7:]
    manifest = json.loads(content.pop(manifest_name))
    config = json.loads(content.pop("blobs/sha256/" + receipt.config_digest[7:]))
    mutate(config, manifest, content)
    config_raw = encode(config)
    manifest["config"].update(digest=digest(config_raw), size=len(config_raw))
    content["blobs/sha256/" + digest(config_raw)[7:]] = config_raw
    manifest_raw = encode(manifest)
    content["blobs/sha256/" + digest(manifest_raw)[7:]] = manifest_raw
    index = json.loads(content["index.json"])
    index["manifests"][0].update(digest=digest(manifest_raw), size=len(manifest_raw))
    content["index.json"] = encode(index)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, raw in sorted(content.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    path.write_bytes(output.getvalue())
    return receipt.model_copy(
        update={
            "config_digest": digest(config_raw),
            "manifest_digest": digest(manifest_raw),
            "archive_sha256": sha256(path.read_bytes()).hexdigest(),
        }
    )


@pytest.mark.parametrize(
    "change", ["root_user", "extra_env", "boolean_history", "layer_source", "layer_mode"]
)
def test_self_consistently_rehashed_image_cannot_weaken_runtime_or_replace_worker(inputs, change):
    receipt = build(inputs)

    def mutate(config, manifest, content):
        if change == "root_user":
            config["config"]["User"] = "0:0"
        elif change == "extra_env":
            config["config"]["Env"].append("SECRET=unapproved")
        elif change == "boolean_history":
            config["history"][0]["empty_layer"] = True
            # The trusted base lacks this field; it must not accept new metadata.
        else:
            old = manifest["layers"][-1]
            layer_raw = content.pop("blobs/sha256/" + old["digest"][7:])
            stream = io.BytesIO()
            with (
                tarfile.open(fileobj=io.BytesIO(layer_raw)) as layer,
                tarfile.open(fileobj=stream, mode="w") as altered,
            ):
                for row in layer:
                    source = layer.extractfile(row).read() if row.isfile() else None
                    if row.name.endswith("worker.py"):
                        if change == "layer_source":
                            source = b"altered worker"
                        else:
                            row.mode = 0o777
                        row.size = len(source)
                    altered.addfile(row, io.BytesIO(source) if source is not None else None)
            raw = stream.getvalue()
            old.update(digest=digest(raw), size=len(raw))
            content["blobs/sha256/" + old["digest"][7:]] = raw
            config["rootfs"]["diff_ids"][-1] = old["digest"]

    changed = rewrite_image(inputs, receipt, mutate)
    with pytest.raises(ValueError):
        check(inputs, changed)


def test_receipt_schema_rejects_execution_authority_and_false_hash_status(inputs):
    receipt = build(inputs)
    from ai_quant_os.strategy_image_contracts import StrategyWorkerImageReceipt

    for field in (
        "image_verified",
        "execution_verified",
        "promotion_allowed",
        "archive_integrity_verified",
    ):
        payload = json.loads(receipt.model_dump_json())
        payload[field] = True
        with pytest.raises(ValueError):
            StrategyWorkerImageReceipt.model_validate_json(json.dumps(payload))


def update_base_config(arguments, config):
    root = arguments["base_metadata"]
    raw = encode(config)
    manifest = json.loads((root / "base_manifest.json").read_bytes())
    manifest["config"].update(digest=digest(raw), size=len(raw))
    manifest_raw = encode(manifest)
    index = json.loads((root / "base_index.json").read_bytes())
    index["manifests"][0].update(digest=digest(manifest_raw), size=len(manifest_raw))
    index_raw = encode(index)
    pin = json.loads((root / "base_image.json").read_bytes())
    pin.update(
        config_digest=digest(raw),
        manifest_digest=digest(manifest_raw),
        index_digest=digest(index_raw),
    )
    for name, content in (
        ("base_config.json", raw),
        ("base_manifest.json", manifest_raw),
        ("base_index.json", index_raw),
        ("base_image.json", encode(pin)),
    ):
        (root / name).write_bytes(content)
    arguments["base_manifest_digest"] = digest(manifest_raw)


def test_integer_cannot_impersonate_boolean_history_in_rehashed_config(inputs):
    arguments = inputs[0]
    config = json.loads((arguments["base_metadata"] / "base_config.json").read_bytes())
    config["history"].append({"created_by": "inert metadata", "empty_layer": True})
    update_base_config(arguments, config)
    receipt = build(inputs)

    def mutate(config, manifest, content):
        config["history"][1]["empty_layer"] = 1

    changed = rewrite_image(inputs, receipt, mutate)
    with pytest.raises(ValueError):
        check(inputs, changed)


@pytest.mark.parametrize("change", ["onbuild", "history", "duplicate_version"])
def test_unsupported_base_runtime_is_refused_even_with_consistent_trusted_metadata(inputs, change):
    arguments = inputs[0]
    config = json.loads((arguments["base_metadata"] / "base_config.json").read_bytes())
    if change == "onbuild":
        config["config"]["OnBuild"] = ["unapproved instruction"]
    elif change == "history":
        config["history"] = None
    else:
        config["config"]["Env"].append("PYTHON_VERSION=3.11.9")
    update_base_config(arguments, config)
    with pytest.raises(ValueError):
        build(inputs)
    assert not arguments["output"].exists()
