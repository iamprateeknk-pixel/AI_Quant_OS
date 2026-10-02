"""Trusted-parent archive inspection; no candidate or behavioral suite execution."""

import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
from hashlib import sha256

import pytest

from test_repair_image_archive import archive_inputs as archive_inputs, assemble
from test_repair_image_inputs import image_inputs as image_inputs


@pytest.fixture
def assembled(archive_inputs):
    receipt = assemble(archive_inputs)
    return archive_inputs[0][2] / "image.oci.tar", archive_inputs[0][0], receipt


def check(assembled, receipt=None):
    assert importlib.util.find_spec("ai_quant_os.repair_image_verification"), (
        "Missing trusted-parent archive check"
    )
    module = importlib.import_module("ai_quant_os.repair_image_verification")
    archive, recipes, original = assembled
    pins = receipt or original
    return module.verify_repair_image_archive(
        archive,
        recipes=recipes,
        archive_sha256=pins["archive_sha256"],
        manifest_digest=pins["manifest_digest"],
        config_digest=pins["config_digest"],
    )


def rewrite(assembled, mutate):
    path, _, receipt = assembled
    with tarfile.open(path) as archive:
        content = [(member.name, archive.extractfile(member).read()) for member in archive]
    content = mutate(content)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in content:
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    raw = stream.getvalue()
    path.write_bytes(raw)
    return {**receipt, "archive_sha256": sha256(raw).hexdigest()}


def test_read_only_archive_check_verifies_pins_and_keeps_execution_unapproved(assembled):
    path, _, pins = assembled
    before = path.read_bytes()
    receipt = check(assembled)
    assert receipt["state"] == "ARCHIVE_HASHES_VERIFIED"
    assert receipt["archive_sha256"] == pins["archive_sha256"]
    assert receipt["manifest_digest"] == pins["manifest_digest"]
    assert receipt["config_digest"] == pins["config_digest"]
    assert receipt["layer_count"] == 2
    assert receipt["archive_integrity_verified"] is True
    assert all(
        receipt[key] is False
        for key in ("image_verified", "execution_verified", "promotion_allowed")
    )
    assert path.read_bytes() == before
    assert "worker_fixture" not in sys.modules


def test_immutable_byte_verifier_agrees_with_file_check(assembled):
    module = importlib.import_module("ai_quant_os.repair_image_verification")
    assert hasattr(module, "verify_repair_image_archive_bytes"), (
        "Missing immutable byte verification"
    )
    archive, recipes, pins = assembled
    assert module.verify_repair_image_archive_bytes(
        archive.read_bytes(),
        recipes=recipes,
        archive_sha256=pins["archive_sha256"],
        manifest_digest=pins["manifest_digest"],
        config_digest=pins["config_digest"],
    ) == check(assembled)


@pytest.mark.parametrize("pin", ["archive_sha256", "manifest_digest", "config_digest"])
def test_independent_pin_mismatch_is_rejected(assembled, pin):
    pins = {**assembled[2], pin: ("" if pin == "archive_sha256" else "sha256:") + "0" * 64}
    with pytest.raises(ValueError):
        check(assembled, pins)


@pytest.mark.parametrize(
    "damage", ["duplicate", "extra", "extra_blob", "path", "layout", "blob", "missing"]
)
def test_altered_archive_members_refuse_even_with_recomputed_outer_hash(assembled, damage):
    def mutate(content):
        if damage == "duplicate":
            return content + [content[0]]
        if damage == "extra":
            return content + [("private.txt", b"must not be accepted")]
        if damage == "extra_blob":
            return content + [("blobs/sha256/" + sha256(b"unused").hexdigest(), b"unused")]
        if damage == "path":
            return content + [("../outside", b"must stay inert")]
        if damage == "missing":
            return content[:-1]
        selected = (
            next(i for i, row in enumerate(content) if row[0] == "oci-layout")
            if damage == "layout"
            else next(i for i, row in enumerate(content) if row[0].startswith("blobs/sha256/"))
        )
        content[selected] = (content[selected][0], b"changed")
        return content

    pins = rewrite(assembled, mutate)
    with pytest.raises(ValueError):
        check(assembled, pins)


def test_changed_server_base_metadata_refuses_existing_archive(assembled):
    (assembled[1] / "base_manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        check(assembled)


def test_hard_linked_archive_is_refused(assembled):
    os.link(assembled[0], assembled[0].with_name("linked.tar"))
    with pytest.raises(ValueError):
        check(assembled)


def test_cli_uses_explicit_pins_and_prints_only_verified_summary(assembled):
    path, recipes, pins = assembled
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.repair_image_verification",
            str(path),
            "--recipes",
            str(recipes),
            "--archive-sha256",
            pins["archive_sha256"],
            "--manifest-digest",
            pins["manifest_digest"],
            "--config-digest",
            pins["config_digest"],
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "ARCHIVE_HASHES_VERIFIED"


@pytest.mark.parametrize("damage", ["user", "entrypoint", "authority", "environment", "diff_id"])
def test_rehashed_image_cannot_bypass_runtime_or_rootfs_profile(assembled, damage):
    pins = dict(assembled[2])

    def mutate(content):
        members = dict(content)
        index = json.loads(members["index.json"])
        old_manifest = "blobs/sha256/" + index["manifests"][0]["digest"][7:]
        manifest = json.loads(members.pop(old_manifest))
        old_config = "blobs/sha256/" + manifest["config"]["digest"][7:]
        config = json.loads(members.pop(old_config))
        if damage == "user":
            config["config"]["User"] = "0:0"
        elif damage == "entrypoint":
            config["config"]["Entrypoint"] = ["/bin/sh"]
        elif damage == "authority":
            config["config"]["Labels"]["ai.quant.repair.promotion-allowed"] = "true"
        elif damage == "environment":
            config["config"]["Env"].append("UNREVIEWED=1")
        else:
            config["rootfs"]["diff_ids"][-1] = "sha256:" + "0" * 64
        raw = json.dumps(config).encode()
        pins["config_digest"] = "sha256:" + sha256(raw).hexdigest()
        members["blobs/sha256/" + pins["config_digest"][7:]] = raw
        manifest["config"].update(digest=pins["config_digest"], size=len(raw))
        raw = json.dumps(manifest).encode()
        pins["manifest_digest"] = "sha256:" + sha256(raw).hexdigest()
        members["blobs/sha256/" + pins["manifest_digest"][7:]] = raw
        index["manifests"][0].update(digest=pins["manifest_digest"], size=len(raw))
        members["index.json"] = json.dumps(index).encode()
        return list(members.items())

    outer = rewrite(assembled, mutate)
    pins["archive_sha256"] = outer["archive_sha256"]
    with pytest.raises(ValueError):
        check(assembled, pins)


def test_duplicate_json_keys_are_rejected(assembled):
    pins = rewrite(
        assembled,
        lambda rows: [
            (
                name,
                b'{"imageLayoutVersion":"1.0.0","imageLayoutVersion":"1.0.0"}'
                if name == "oci-layout"
                else raw,
            )
            for name, raw in rows
        ],
    )
    with pytest.raises(ValueError):
        check(assembled, pins)


def test_nonzero_trailing_archive_bytes_are_rejected(assembled):
    raw = assembled[0].read_bytes() + b"unexpected trailing bytes"
    assembled[0].write_bytes(raw)
    pins = {**assembled[2], "archive_sha256": sha256(raw).hexdigest()}
    with pytest.raises(ValueError):
        check(assembled, pins)
