"""Pinned archive and simulated daemon responses; no image or candidate execution."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
import jsonschema

from ai_quant_os.repair_transport import CommandResult
from test_strategy_image_archive import inputs as inputs


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_image_loading"), (
        "Pinned strategy image handoff is required"
    )
    return importlib.import_module("ai_quant_os.strategy_image_loading")


@pytest.fixture
def assembled(inputs):
    from ai_quant_os.strategy_image_archive import assemble_strategy_worker_image

    arguments, marker = inputs
    receipt = assemble_strategy_worker_image(**arguments)
    return arguments, marker, receipt


def arguments(assembled):
    original, _, receipt = assembled
    return dict(
        archive=original["output"] / "image.oci.tar",
        base_metadata=original["base_metadata"],
        base_manifest_digest=receipt.base_manifest_digest,
        worker_sha256=receipt.worker_sha256,
        archive_sha256=receipt.archive_sha256,
        manifest_digest=receipt.manifest_digest,
        config_digest=receipt.config_digest,
    )


@pytest.fixture
def daemon(assembled, monkeypatch):
    loader = module()
    args = arguments(assembled)
    with tarfile.open(args["archive"]) as archive:
        config = json.load(archive.extractfile("blobs/sha256/" + args["config_digest"][7:]))
        size = archive.getmember("blobs/sha256/" + args["manifest_digest"][7:]).size
    image = {
        "Id": args["manifest_digest"],
        "Os": "linux",
        "Architecture": "amd64",
        "Descriptor": {
            "digest": args["manifest_digest"],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "size": size,
        },
        "Config": config["config"],
        "RootFS": {"Type": "layers", "Layers": config["rootfs"]["diff_ids"]},
    }
    engine = {
        "ID": "test-engine-1",
        "OSType": "linux",
        "CgroupVersion": "2",
        "MemoryLimit": True,
        "SwapLimit": True,
        "PidsLimit": True,
        "CpuCfsQuota": True,
        "SecurityOptions": ["name=seccomp,profile=builtin"],
        "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
    }
    version = {"ApiVersion": "1.49"}
    calls, failures = [], {}

    def command(executable, *command, **options):
        calls.append((command, options))
        stage = command[0] if command[0] in ("info", "version") else command[1]
        if stage in failures:
            failure = failures[stage]
            if isinstance(failure, Exception):
                raise failure
            return failure
        if stage == "info":
            response = engine
        elif stage == "version":
            response = version
        elif stage == "inspect":
            response = image
        else:
            assert stage == "load"
            return CommandResult(0, b"not identity evidence", b"")
        return CommandResult(0, json.dumps(response).encode(), b"")

    monkeypatch.setattr(loader.shutil, "which", lambda name: "trusted-docker")
    monkeypatch.setattr(loader, "local_docker_command", command)
    return calls, engine, version, image, failures


def test_load_checks_exact_bytes_and_inspects_manifest_without_start(assembled, daemon):
    args = arguments(assembled)
    raw = args["archive"].read_bytes()
    receipt = module().load_strategy_worker_image(**args)
    calls = daemon[0]
    assert [entry[0][:2] for entry in calls] == [
        ("info", "--format"),
        ("version", "--format"),
        ("image", "load"),
        ("image", "inspect"),
    ]
    assert calls[2] == (
        ("image", "load", "--quiet", "--platform", "linux/amd64"),
        {"input_bytes": raw, "timeout": 60},
    )
    assert calls[3][0] == (
        "image",
        "inspect",
        args["manifest_digest"],
        "--platform",
        "linux/amd64",
        "--format",
        "{{json .}}",
    )
    assert (
        receipt.state == "IMAGE_LOAD_INSPECTED"
        and receipt.loaded_image_id == args["manifest_digest"]
    )
    assert receipt.archive.state == "ARCHIVE_HASHES_VERIFIED"
    assert receipt.engine_id == "test-engine-1"
    assert receipt.execution_verified is False and receipt.promotion_allowed is False
    assert receipt.archive.image_verified is False
    assert not assembled[1].exists()


def test_probe_is_read_only_and_does_not_claim_loaded_execution(assembled, daemon):
    result = module().inspect_strategy_worker_image(**arguments(assembled))
    assert result.state == "IMAGE_PRESENT_INSPECTED"
    assert [entry[0][0] for entry in daemon[0]] == ["info", "version", "image"]
    assert not result.execution_verified


@pytest.mark.parametrize(
    "pin",
    ["worker_sha256", "base_manifest_digest", "archive_sha256", "manifest_digest", "config_digest"],
)
def test_bad_independent_pin_prevents_all_daemon_calls(assembled, daemon, pin):
    args = arguments(assembled)
    args[pin] = ("" if pin.endswith("sha256") else "sha256:") + "0" * 64
    with pytest.raises(ValueError):
        module().load_strategy_worker_image(**args)
    assert daemon[0] == []


def test_replaced_path_cannot_change_verified_import_bytes(assembled, daemon, monkeypatch):
    loader = module()
    args = arguments(assembled)
    raw = args["archive"].read_bytes()
    original = loader.local_docker_command

    def replace(executable, *command, **options):
        if command[0] == "info":
            args["archive"].write_bytes(b"replacement")
        return original(executable, *command, **options)

    monkeypatch.setattr(loader, "local_docker_command", replace)
    loader.load_strategy_worker_image(**args)
    assert daemon[0][2][1]["input_bytes"] == raw


@pytest.mark.parametrize(
    "change",
    ["windows", "cgroup", "memory", "swap", "pids", "cpu", "seccomp", "classic", "identity", "api"],
)
def test_unsupported_engine_is_refused_before_load(assembled, daemon, change):
    engine, version = daemon[1:3]
    key = {
        "windows": "OSType",
        "cgroup": "CgroupVersion",
        "memory": "MemoryLimit",
        "swap": "SwapLimit",
        "pids": "PidsLimit",
        "cpu": "CpuCfsQuota",
        "seccomp": "SecurityOptions",
        "classic": "DriverStatus",
        "identity": "ID",
    }
    if change == "api":
        version["ApiVersion"] = "1.48"
    else:
        engine[key[change]] = None
    with pytest.raises(ValueError):
        module().load_strategy_worker_image(**arguments(assembled))
    assert all(call[0][:2] != ("image", "load") for call in daemon[0])


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "platform",
        "descriptor",
        "size",
        "layer",
        "root_type",
        "user",
        "entrypoint",
        "command",
        "env",
        "workdir",
        "label",
        "onbuild",
        "volume",
        "healthcheck",
        "unknown",
        "boolean",
    ],
)
def test_loaded_substitution_is_uncertain_and_never_retried(assembled, daemon, change):
    image = daemon[3]
    if change == "id":
        image["Id"] = "sha256:" + "0" * 64
    elif change == "platform":
        image["Architecture"] = "arm64"
    elif change == "descriptor":
        image["Descriptor"]["digest"] = "sha256:" + "0" * 64
    elif change == "size":
        image["Descriptor"]["size"] += 1
    elif change == "layer":
        image["RootFS"]["Layers"][-1] = "sha256:" + "0" * 64
    elif change == "root_type":
        image["RootFS"]["Type"] = "not-layers"
    elif change == "user":
        image["Config"]["User"] = "0:0"
    elif change == "entrypoint":
        image["Config"]["Entrypoint"] = ["/bin/sh"]
    elif change == "command":
        image["Config"]["Cmd"].append("untrusted")
    elif change == "env":
        image["Config"]["Env"].append("SECRET=unapproved")
    elif change == "workdir":
        image["Config"]["WorkingDir"] = "/"
    elif change == "label":
        image["Config"]["Labels"]["ai.quant.strategy.execution-verified"] = "true"
    elif change == "onbuild":
        image["Config"]["OnBuild"] = ["RUN unapproved"]
    elif change == "volume":
        image["Config"]["Volumes"] = {"/job": {}}
    elif change == "healthcheck":
        image["Config"]["Healthcheck"] = {"Test": ["CMD", "unapproved"]}
    elif change == "unknown":
        image["Config"]["UnknownSetting"] = "unapproved"
    else:
        image["Config"]["ArgsEscaped"] = 0
    with pytest.raises(RuntimeError, match="uncertain"):
        module().load_strategy_worker_image(**arguments(assembled))
    assert len(daemon[0]) == 4
    assert not assembled[1].exists()


@pytest.mark.parametrize("stage", ["load", "inspect"])
@pytest.mark.parametrize(
    "failure",
    [
        CommandResult(1, b"", b"private detail"),
        TimeoutError("private detail"),
        ValueError("private detail"),
    ],
)
def test_ambiguous_mutation_retains_input_and_never_retries_or_removes(
    assembled, daemon, stage, failure
):
    daemon[4][stage] = failure
    with pytest.raises(RuntimeError, match="uncertain") as error:
        module().load_strategy_worker_image(**arguments(assembled))
    assert "private detail" not in str(error.value)
    assert sum(call[0][:2] == ("image", "load") for call in daemon[0]) == 1
    assert arguments(assembled)["archive"].exists()


def test_malformed_or_duplicate_metadata_is_refused_before_mutation(assembled, daemon, monkeypatch):
    monkeypatch.setattr(
        module(),
        "local_docker_command",
        lambda *args, **kwargs: CommandResult(0, b'{"ID":"first","ID":"second"}', b""),
    )
    with pytest.raises(ValueError):
        module().load_strategy_worker_image(**arguments(assembled))


def test_missing_executable_prevents_engine_access(assembled, daemon, monkeypatch):
    monkeypatch.setattr(module().shutil, "which", lambda name: None)
    with pytest.raises(ValueError):
        module().load_strategy_worker_image(**arguments(assembled))
    assert daemon[0] == []


def test_wire_receipt_refuses_true_authority_and_changed_nested_state(assembled, daemon):
    result = module().load_strategy_worker_image(**arguments(assembled))
    payload = json.loads(result.model_dump_json())
    for field in ("execution_verified", "promotion_allowed"):
        with pytest.raises(ValueError):
            type(result).model_validate({**payload, field: True})
    payload["archive"]["state"] = "OCI_ARCHIVE_ONLY"
    payload["archive"]["archive_integrity_verified"] = False
    with pytest.raises(ValueError):
        type(result).model_validate(payload)


def test_operator_cli_missing_engine_refuses_without_traceback(assembled, tmp_path):
    args = arguments(assembled)
    command = [sys.executable, "-m", "ai_quant_os.strategy_image_loading", "inspect"]
    for name, value in args.items():
        command.extend(["--" + name.replace("_", "-"), str(value)])
    result = subprocess.run(command, capture_output=True, timeout=20)
    assert result.returncode == 2
    assert b"refused" in result.stderr.lower() and b"Traceback" not in result.stderr
    assert result.stdout == b"" and not assembled[1].exists()


def test_retained_handoff_schema_matches_model_and_denies_execution_authority(assembled, daemon):
    result = module().load_strategy_worker_image(**arguments(assembled))
    schema_path = (
        Path(__file__).resolve().parents[1] / "docs/schemas/strategy-image-handoff.schema.json"
    )
    assert schema_path.is_file(), "Image handoff wire schema is required"
    schema = json.loads(schema_path.read_bytes())
    assert schema == type(result).model_json_schema()
    payload = result.model_dump(mode="json")
    jsonschema.validate(payload, schema)
    for field in ("execution_verified", "promotion_allowed"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**payload, field: True}, schema)


def test_docker_inert_optional_defaults_are_admitted_without_weakening_runtime(assembled, daemon):
    daemon[3]["Config"].update(
        Hostname="",
        Domainname="",
        Image="",
        AttachStdin=False,
        AttachStdout=False,
        AttachStderr=False,
        Tty=False,
        OpenStdin=False,
        StdinOnce=False,
        ArgsEscaped=False,
        OnBuild=None,
        Volumes=None,
        ExposedPorts=None,
        Healthcheck=None,
        Shell=None,
        StopSignal="",
        StopTimeout=None,
    )
    assert (
        module().inspect_strategy_worker_image(**arguments(assembled)).state
        == "IMAGE_PRESENT_INSPECTED"
    )


@pytest.mark.parametrize("raw", [None, "archive", bytearray(b"archive"), b""])
def test_byte_verifier_refuses_nonimmutable_or_empty_snapshots_before_daemon_access(
    assembled, daemon, raw
):
    from ai_quant_os.strategy_image_verification import verify_strategy_worker_image_bytes

    pins = arguments(assembled)
    pins.pop("archive")
    with pytest.raises(ValueError):
        verify_strategy_worker_image_bytes(raw, **pins)
    assert daemon[0] == []
