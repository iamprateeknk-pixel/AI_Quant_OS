"""Trusted archive/transport fixtures; no image or candidate code is executed."""

import importlib
import json
import os
import subprocess
import sys

import pytest

from ai_quant_os.repair_transport import CommandResult
from test_repair_image_archive import archive_inputs as archive_inputs
from test_repair_image_inputs import image_inputs as image_inputs
from test_repair_image_verification import assembled as assembled
from test_repair_sandbox import engine


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_image_loading"), (
        "Missing pinned image import"
    )
    return importlib.import_module("ai_quant_os.repair_image_loading")


def load(assembled):
    archive, recipes, receipt = assembled
    return module().load_repair_image_archive(
        archive,
        recipes=recipes,
        archive_sha256=receipt["archive_sha256"],
        manifest_digest=receipt["manifest_digest"],
        config_digest=receipt["config_digest"],
    )


@pytest.fixture
def daemon(assembled, monkeypatch):
    loader = module()
    metadata = {**engine(), "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
    inspection = {"Id": assembled[2]["manifest_digest"], "Os": "linux", "Architecture": "amd64"}
    version = {"ApiVersion": "1.49"}
    calls = []
    failures = {}

    def command(executable, *arguments, **options):
        calls.append((executable, arguments, options))
        stage = arguments[0] if arguments[0] in ("info", "version") else arguments[1]
        if stage in failures:
            failure = failures[stage]
            if isinstance(failure, Exception):
                raise failure
            return failure
        if stage == "info":
            return CommandResult(0, json.dumps(metadata).encode(), b"")
        if stage == "version":
            return CommandResult(0, json.dumps(version).encode(), b"")
        if stage == "load":
            return CommandResult(0, b"daemon output is not identity evidence", b"")
        assert stage == "inspect"
        return CommandResult(0, json.dumps(inspection).encode(), b"")

    monkeypatch.setattr(loader.shutil, "which", lambda name: "owned-docker")
    monkeypatch.setattr(loader, "local_docker_command", command)
    return calls, metadata, inspection, failures, version


def test_verified_bytes_only_are_sent_and_exact_id_is_inspected(assembled, daemon):
    before = assembled[0].read_bytes()
    result = load(assembled)
    calls = daemon[0]
    assert [row[1][0:2] for row in calls] == [
        ("info", "--format"),
        ("version", "--format"),
        ("image", "load"),
        ("image", "inspect"),
    ]
    assert calls[1][1] == ("version", "--format", "{{json .Server}}")
    assert calls[2][1] == ("image", "load", "--quiet", "--platform", "linux/amd64")
    assert calls[2][2] == {"input_bytes": before, "timeout": 60}
    assert calls[3][1] == (
        "image",
        "inspect",
        assembled[2]["manifest_digest"],
        "--platform",
        "linux/amd64",
        "--format",
        "{{json .}}",
    )
    assert result["state"] == "IMAGE_LOAD_INSPECTED"
    assert result["loaded_image_id"] == assembled[2]["manifest_digest"]
    assert result["archive_integrity_verified"] is True
    assert all(
        result[key] is False
        for key in ("image_verified", "execution_verified", "promotion_allowed")
    )
    assert assembled[0].read_bytes() == before
    assert "worker_fixture" not in sys.modules


def test_file_replaced_after_verification_cannot_replace_loaded_bytes(
    assembled, daemon, monkeypatch
):
    loader = module()
    original = loader.local_docker_command
    before = assembled[0].read_bytes()

    def replace_file(executable, *arguments, **options):
        if arguments[0] == "info":
            assembled[0].write_bytes(b"changed after verification")
        return original(executable, *arguments, **options)

    monkeypatch.setattr(loader, "local_docker_command", replace_file)
    assert load(assembled)["loaded_image_id"] == assembled[2]["manifest_digest"]
    assert daemon[0][2][2]["input_bytes"] == before


@pytest.mark.parametrize("pin", ["archive_sha256", "manifest_digest", "config_digest"])
def test_bad_pin_prevents_every_daemon_call(assembled, daemon, pin):
    assembled[2][pin] = ("" if pin == "archive_sha256" else "sha256:") + "0" * 64
    with pytest.raises(ValueError):
        load(assembled)
    assert daemon[0] == []


@pytest.mark.parametrize("damage", ["legacy", "no_store", "windows", "resource"])
def test_unsupported_engine_cannot_load(assembled, daemon, damage):
    metadata = daemon[1]
    if damage == "legacy":
        metadata["DriverStatus"] = [["Backing Filesystem", "extfs"]]
    elif damage == "no_store":
        del metadata["DriverStatus"]
    elif damage == "windows":
        metadata["OSType"] = "windows"
    else:
        metadata["MemoryLimit"] = False
    with pytest.raises(ValueError):
        load(assembled)
    assert len(daemon[0]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        CommandResult(1, b"failed", b"private detail"),
        TimeoutError("late"),
        ValueError("too much output"),
    ],
)
def test_ambiguous_load_is_not_retried_or_removed(assembled, daemon, failure):
    daemon[3]["load"] = failure
    with pytest.raises(RuntimeError, match="uncertain"):
        load(assembled)
    assert len(daemon[0]) == 3
    assert assembled[0].exists()


@pytest.mark.parametrize(
    "field,value", [("Id", "sha256:" + "0" * 64), ("Os", "windows"), ("Architecture", "arm64")]
)
def test_loaded_inspection_mismatch_is_uncertain_and_never_admitted(
    assembled, daemon, field, value
):
    daemon[2][field] = value
    with pytest.raises(RuntimeError, match="uncertain"):
        load(assembled)
    assert len(daemon[0]) == 4


def test_inspection_failure_retains_archive_without_reload_or_cleanup(assembled, daemon):
    daemon[3]["inspect"] = CommandResult(1, b"", b"private failure")
    with pytest.raises(RuntimeError, match="uncertain"):
        load(assembled)
    assert len(daemon[0]) == 4
    assert assembled[0].exists()


def test_absent_engine_prevents_load(assembled, daemon):
    daemon[3]["info"] = CommandResult(1, b"", b"unavailable")
    with pytest.raises(ValueError):
        load(assembled)
    assert len(daemon[0]) == 1


@pytest.mark.parametrize("version", ["1.48", "1", "1.49.0", "bad", True, None])
def test_unsupported_platform_inspection_api_prevents_load(assembled, daemon, version):
    daemon[4]["ApiVersion"] = version
    with pytest.raises(ValueError):
        load(assembled)
    assert len(daemon[0]) == 2


def test_real_cli_rejects_bad_pin_without_daemon_access(assembled):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.repair_image_loading",
            str(assembled[0]),
            "--recipes",
            str(assembled[1]),
            "--archive-sha256",
            "0" * 64,
            "--manifest-digest",
            assembled[2]["manifest_digest"],
            "--config-digest",
            assembled[2]["config_digest"],
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Image import refused" in result.stderr
    assert b"Traceback" not in result.stderr
