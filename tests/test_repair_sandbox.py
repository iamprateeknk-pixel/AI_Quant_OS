import copy
import importlib

import pytest


IMAGE = "sha256:" + "0" * 64
SUITE = "1" * 64


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_sandbox") is not None, (
        "Missing OS confinement admission for repair verification"
    )
    return importlib.import_module("ai_quant_os.repair_sandbox")


def engine():
    return {
        "OSType": "linux",
        "CgroupVersion": "2",
        "MemoryLimit": True,
        "SwapLimit": True,
        "PidsLimit": True,
        "CpuCfsQuota": True,
        "SecurityOptions": ["name=seccomp,profile=builtin"],
    }


def container(job):
    return {
        "Image": IMAGE,
        "Config": {
            "User": "65534:65534",
            "WorkingDir": "/scratch",
            "Labels": {"ai-quant.repair-owner": "test-owner"},
            "Entrypoint": ["/usr/bin/env"],
            "Cmd": [
                "-i",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/scratch/home",
                "TMPDIR=/scratch",
                "/usr/local/bin/python",
                "-I",
                "-B",
                "/job/verifier.py",
                "/job/request.json",
            ],
        },
        "HostConfig": {
            "NetworkMode": "none",
            "IpcMode": "none",
            "PidMode": "",
            "UTSMode": "",
            "CgroupnsMode": "private",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "Devices": [],
            "DeviceRequests": None,
            "Binds": None,
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 536870912,
            "MemorySwap": 536870912,
            "NanoCpus": 1000000000,
            "PidsLimit": 64,
            "LogConfig": {"Type": "none", "Config": {}},
            "Tmpfs": {"/scratch": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
            "Ulimits": [
                {"Name": "cpu", "Soft": 30, "Hard": 30},
                {"Name": "fsize", "Soft": 1048576, "Hard": 1048576},
                {"Name": "nofile", "Soft": 128, "Hard": 128},
                {"Name": "nproc", "Soft": 64, "Hard": 64},
            ],
        },
        "Mounts": [
            {"Type": "bind", "Source": str(job.resolve()), "Destination": "/job", "RW": False}
        ],
        "State": {"Running": False, "Status": "created"},
    }


@pytest.mark.parametrize("key", ["MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota"])
def test_engine_without_required_kernel_limit_support_is_denied(key):
    policy = module().DockerConfinement(IMAGE, SUITE)
    evidence = engine()
    evidence[key] = False
    with pytest.raises(ValueError, match="enforcement"):
        policy.require_engine(evidence)


def test_seccomp_and_linux_cgroup_isolation_are_mandatory():
    policy = module().DockerConfinement(IMAGE, SUITE)
    for key, value in [("OSType", "windows"), ("CgroupVersion", "1"), ("SecurityOptions", [])]:
        evidence = engine()
        evidence[key] = value
        with pytest.raises(ValueError, match="enforcement"):
            policy.require_engine(evidence)
    policy.require_engine(engine())


@pytest.mark.parametrize(
    "key,value",
    [
        ("NetworkMode", "host"),
        ("IpcMode", "host"),
        ("PidMode", "host"),
        ("Privileged", True),
        ("ReadonlyRootfs", False),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("NanoCpus", 0),
        ("PidsLimit", -1),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("CapAdd", ["SYS_ADMIN"]),
        ("Tmpfs", {"/scratch": "rw"}),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
    ],
)
def test_weakened_created_container_is_never_admitted(tmp_path, key, value):
    policy = module().DockerConfinement(IMAGE, SUITE)
    evidence = container(tmp_path)
    evidence["HostConfig"][key] = value
    with pytest.raises(ValueError, match="confinement"):
        policy.require_container(evidence, owner="test-owner", job=tmp_path)


def test_wrong_owner_or_extra_writable_mount_denies_execution(tmp_path):
    policy = module().DockerConfinement(IMAGE, SUITE)
    evidence = container(tmp_path)
    with pytest.raises(ValueError, match="confinement"):
        policy.require_container(evidence, owner="someone-else", job=tmp_path)
    evidence["Mounts"].append(
        {"Type": "bind", "Source": "/home", "Destination": "/host", "RW": True}
    )
    with pytest.raises(ValueError, match="confinement"):
        policy.require_container(evidence, owner="test-owner", job=tmp_path)
    policy.require_container(container(tmp_path), owner="test-owner", job=tmp_path)


def test_image_and_owned_verifier_command_cannot_be_replaced(tmp_path):
    policy = module().DockerConfinement(IMAGE, SUITE)
    for section, key, value in [
        (None, "Image", "sha256:" + "2" * 64),
        ("Config", "User", "root"),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("Config", "Cmd", ["candidate-supplied-command"]),
    ]:
        evidence = copy.deepcopy(container(tmp_path))
        (evidence if section is None else evidence[section])[key] = value
        with pytest.raises(ValueError, match="confinement"):
            policy.require_container(evidence, owner="test-owner", job=tmp_path)


def test_real_probe_missing_image_or_unavailable_engine_never_claims_sandbox_ready():
    probe = module().DockerConfinement(IMAGE, SUITE).probe()
    assert probe.available is False
    assert probe.reason in {
        "DOCKER_UNAVAILABLE",
        "ENGINE_UNAVAILABLE",
        "ENGINE_UNSUPPORTED",
        "IMAGE_UNAVAILABLE",
    }
    assert probe.promotion_allowed is False
