"""Fixed unstarted strategy container recipe and metadata admission."""

from copy import deepcopy
from pathlib import Path
import re

from ai_quant_os.repair_sandbox import (
    DockerConfinement,
    MEMORY_BYTES,
    SCRATCH_OPTIONS,
    VERIFIER_COMMAND,
)
from ai_quant_os.strategy_container_contracts import StrategyContainerOwnership
from ai_quant_os.strategy_image_content import CMD as IMAGE_COMMAND, LABELS, encode_metadata

CMD = tuple(IMAGE_COMMAND)


def strategy_labels(ownership: StrategyContainerOwnership) -> dict:
    return {
        **LABELS,
        "ai-quant.strategy-owner": ownership.owner,
        "ai-quant.strategy-invocation": ownership.invocation_ref,
        "ai-quant.strategy-stage": ownership.stage_sha256,
        "ai-quant.strategy-worker": ownership.worker_sha256,
    }


def strategy_create_arguments(ownership: StrategyContainerOwnership) -> tuple[str, ...]:
    source = ownership.job_path
    if any(char in source for char in (",", "\n", "\r", '"')):
        raise ValueError("Strategy mount path cannot be represented safely")
    labels = tuple(
        part
        for key, value in sorted(strategy_labels(ownership).items())
        for part in ("--label", key + "=" + value)
    )
    return (
        "container",
        "create",
        "--name",
        ownership.container_name,
        *labels,
        "--pull",
        "never",
        "--read-only",
        "--user",
        "65534:65534",
        "--workdir",
        "/scratch",
        "--entrypoint",
        "/usr/bin/env",
        "--network",
        "none",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        str(MEMORY_BYTES),
        "--memory-swap",
        str(MEMORY_BYTES),
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--restart",
        "no",
        "--tmpfs",
        "/scratch:" + SCRATCH_OPTIONS,
        "--ulimit",
        "cpu=30:30",
        "--ulimit",
        "fsize=1048576:1048576",
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "nproc=64:64",
        "--mount",
        "type=bind,source=" + source + ",target=/job,readonly",
        ownership.image_id,
        *CMD,
    )


def require_strategy_container_owner(
    value: dict, ownership: StrategyContainerOwnership, identity: str
) -> str:
    try:
        cid = value["Id"]
        if (
            type(cid) is not str
            or not re.fullmatch("[a-f0-9]{64}", cid)
            or identity not in (cid, ownership.container_name)
            or value["Name"] != "/" + ownership.container_name
            or value["Image"] != ownership.image_id
            or encode_metadata(value["Config"]["Labels"])
            != encode_metadata(strategy_labels(ownership))
        ):
            raise ValueError("Strategy container ownership mismatch")
        return cid
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Strategy container ownership metadata invalid") from error


def require_created_strategy_container(
    value: dict, ownership: StrategyContainerOwnership, environment: tuple[str, ...]
) -> None:
    """Advertised created-state checks, never proof of running kernel enforcement."""
    try:
        config, host = value["Config"], value["HostConfig"]
        expected = {
            "User": "65534:65534",
            "WorkingDir": "/scratch",
            "Image": ownership.image_id,
            "Labels": strategy_labels(ownership),
            "Entrypoint": ["/usr/bin/env"],
            "Cmd": list(CMD),
            "Env": list(environment),
            "Healthcheck": {"Test": ["NONE"]},
        }
        for key, wanted in expected.items():
            if encode_metadata({"value": config[key]}) != encode_metadata({"value": wanted}):
                raise ValueError("Strategy created configuration differs from fixed recipe")
        for key in ("Tty", "OpenStdin", "StdinOnce", "AttachStdin", "AttachStdout", "AttachStderr"):
            if config.get(key, False) is not False:
                raise ValueError("Strategy interactive/attached configuration denied")
        config_defaults = {
            "Domainname": ("",),
            "StopSignal": ("", "SIGTERM"),
            "StopTimeout": (None, 10),
            "NetworkDisabled": (True, False),
            "MacAddress": ("",),
            "ArgsEscaped": (False,),
        }
        known_config = set(expected) | {
            "Tty",
            "OpenStdin",
            "StdinOnce",
            "AttachStdin",
            "AttachStdout",
            "AttachStderr",
            "Volumes",
            "OnBuild",
            "ExposedPorts",
        }
        for key, actual in config.items():
            if key == "Hostname":
                if actual != value["Id"][:12]:
                    raise ValueError("Strategy custom hostname denied")
            elif key not in known_config and (
                key not in config_defaults
                or not any(
                    encode_metadata({"value": actual}) == encode_metadata({"value": wanted})
                    for wanted in config_defaults[key]
                )
            ):
                raise ValueError("Strategy unknown or active configuration denied")
        if (
            config.get("Volumes") not in (None, {})
            or config.get("OnBuild") not in (None, [])
            or config.get("ExposedPorts") not in (None, {})
        ):
            raise ValueError("Strategy active inherited configuration denied")
        required = {
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": False,
            "Mounts": [
                {"Type": "bind", "Source": ownership.job_path, "Target": "/job", "ReadOnly": True}
            ],
        }
        for key, wanted in required.items():
            if encode_metadata({"value": host[key]}) != encode_metadata({"value": wanted}):
                raise ValueError("Strategy created lifecycle/mount recipe differs")
        if host.get("Init") not in (None, False) or type(host.get("Init")) is int:
            raise ValueError("Strategy alternate init denied")
        for entry in host["Ulimits"]:
            if type(entry["Soft"]) is not int or type(entry["Hard"]) is not int:
                raise ValueError("Strategy typed resource limit denied")
        # Reuse the established common kernel/resource predicates after independently
        # checking the strategy's actual program and complete owner labels above.
        common = deepcopy(value)
        common["Config"]["Cmd"] = list(VERIFIER_COMMAND)
        common["Config"]["Labels"]["ai-quant.repair-owner"] = ownership.owner
        DockerConfinement(ownership.image_id, ownership.worker_sha256).require_container(
            common, owner=ownership.owner, job=Path(ownership.job_path)
        )
        if value["Mounts"][0].get("Propagation", "rprivate") != "rprivate":
            raise ValueError("Strategy propagated mount denied")
        for key in (
            "Dns",
            "DnsOptions",
            "DnsSearch",
            "ExtraHosts",
            "Links",
            "PortBindings",
            "MaskedPaths",
            "ReadonlyPaths",
            "Annotations",
        ):
            if (
                key in host
                and key not in ("MaskedPaths", "ReadonlyPaths")
                and host[key] not in (None, [], {})
            ):
                raise ValueError("Strategy extra host configuration denied")
        known = {
            "NetworkMode",
            "IpcMode",
            "PidMode",
            "UTSMode",
            "CgroupnsMode",
            "ReadonlyRootfs",
            "Privileged",
            "CapAdd",
            "CapDrop",
            "Devices",
            "DeviceRequests",
            "Binds",
            "SecurityOpt",
            "Memory",
            "MemorySwap",
            "NanoCpus",
            "PidsLimit",
            "LogConfig",
            "Tmpfs",
            "Ulimits",
            "RestartPolicy",
            "AutoRemove",
            "Init",
            "Mounts",
            "VolumesFrom",
            "GroupAdd",
            "Sysctls",
            "CgroupParent",
        }
        neutral = {
            "CpuShares": (0,),
            "CpuPeriod": (0,),
            "CpuQuota": (0,),
            "CpuRealtimePeriod": (0,),
            "CpuRealtimeRuntime": (0,),
            "CpusetCpus": ("",),
            "CpusetMems": ("",),
            "MemoryReservation": (0,),
            "MemorySwappiness": (None,),
            "OomKillDisable": (None, False),
            "OomScoreAdj": (0,),
            "PublishAllPorts": (False,),
            "Runtime": ("", "runc"),
            "UsernsMode": ("",),
            "Isolation": ("", "default"),
            "ContainerIDFile": ("",),
            "VolumeDriver": ("",),
            "StorageOpt": (None, {}),
            "Dns": (None, []),
            "DnsOptions": (None, []),
            "DnsSearch": (None, []),
            "ExtraHosts": (None, []),
            "Links": (None, []),
            "PortBindings": (None, {}),
            "Annotations": (None, {}),
            "ConsoleSize": ([0, 0],),
            "ShmSize": (67108864,),
            "BlkioWeight": (0,),
            "BlkioWeightDevice": (None, []),
            "BlkioDeviceReadBps": (None, []),
            "BlkioDeviceWriteBps": (None, []),
            "BlkioDeviceReadIOps": (None, []),
            "BlkioDeviceWriteIOps": (None, []),
            "DeviceCgroupRules": (None, []),
            "NetworkDisabled": (True,),
            "CpuCount": (0,),
            "CpuPercent": (0,),
            "IOMaximumIOps": (0,),
            "IOMaximumBandwidth": (0,),
            "KernelMemory": (0,),
            "KernelMemoryTCP": (0,),
            "MaskedPaths": (
                [
                    "/proc/asound",
                    "/proc/acpi",
                    "/proc/interrupts",
                    "/proc/kcore",
                    "/proc/keys",
                    "/proc/latency_stats",
                    "/proc/timer_list",
                    "/proc/timer_stats",
                    "/proc/sched_debug",
                    "/proc/scsi",
                    "/sys/firmware",
                    "/sys/devices/virtual/powercap",
                ],
            ),
            "ReadonlyPaths": (
                ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"],
            ),
        }
        for key, actual in host.items():
            if key not in known and (
                key not in neutral
                or not any(
                    encode_metadata({"value": actual}) == encode_metadata({"value": wanted})
                    for wanted in neutral[key]
                )
            ):
                raise ValueError("Strategy unknown or active host feature denied")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Strategy created confinement metadata invalid") from error
