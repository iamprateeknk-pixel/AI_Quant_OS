"""Fail-closed Docker confinement admission for future isolated repair checks.

Capability advertisements and created-container inspection are prerequisites,
not proof of candidate behavior or a verification certificate. Nothing is started.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_quant_os.repair_transport import bounded_command


MEMORY_BYTES = 536870912
SCRATCH_OPTIONS = "rw,noexec,nosuid,nodev,size=67108864,mode=1777"
VERIFIER_COMMAND = (
    "-i",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "HOME=/scratch/home",
    "TMPDIR=/scratch",
    "/usr/local/bin/python",
    "-I",
    "-B",
    "/job/verifier.py",
    "/job/request.json",
)


@dataclass(frozen=True)
class SandboxProbe:
    available: bool
    reason: str
    image_id: str
    suite_sha256: str
    promotion_allowed: Literal[False] = False


class DockerConfinement:
    """Server-owned immutable image/suite pins; no configurable weaker limits."""

    def __init__(self, image_id: str, suite_sha256: str) -> None:
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("Repair sandbox requires a local immutable image ID")
        if not isinstance(suite_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", suite_sha256):
            raise ValueError("Repair sandbox requires an owned verifier source pin")
        self.image_id, self.suite_sha256 = image_id, suite_sha256

    @staticmethod
    def require_engine(evidence: dict) -> None:
        if (
            not isinstance(evidence, dict)
            or evidence.get("OSType") != "linux"
            or evidence.get("CgroupVersion") != "2"
            or any(
                evidence.get(key) is not True
                for key in (
                    "MemoryLimit",
                    "SwapLimit",
                    "PidsLimit",
                    "CpuCfsQuota",
                )
            )
            or not isinstance(evidence.get("SecurityOptions"), list)
            or "name=seccomp,profile=builtin" not in evidence["SecurityOptions"]
        ):
            raise ValueError("Required sandbox kernel enforcement is unavailable")

    @staticmethod
    def _job_sources(job: Path) -> set[str]:
        # Docker Desktop may translate a Windows bind path to its VM mount path.
        # All accepted representations identify this exact owned job directory.
        absolute = job.resolve()
        sources = {str(absolute), absolute.as_posix()}
        if os.name == "nt" and re.fullmatch(r"[a-zA-Z]:", absolute.drive):
            relative = absolute.as_posix()[3:]
            drive = absolute.drive[0].lower()
            sources.update(
                {f"/run/desktop/mnt/host/{drive}/{relative}", f"/host_mnt/{drive}/{relative}"}
            )
        return sources

    def require_container(self, evidence: dict, *, owner: str, job: Path) -> None:
        """Check actual created-container metadata before any future start operation."""
        try:
            config, host = evidence["Config"], evidence["HostConfig"]
            if (
                not isinstance(owner, str)
                or not 1 <= len(owner) <= 128
                or evidence["Image"] != self.image_id
                or config["User"] != "65534:65534"
                or config["WorkingDir"] != "/scratch"
                or config["Labels"]["ai-quant.repair-owner"] != owner
                or config["Entrypoint"] != ["/usr/bin/env"]
                or config["Cmd"] != list(VERIFIER_COMMAND)
                or evidence["State"]["Running"] is not False
                or evidence["State"]["Status"] != "created"
            ):
                raise ValueError("Repair container confinement identity failed")
            required = {
                "NetworkMode": "none",
                "IpcMode": "none",
                "PidMode": "",
                "UTSMode": "",
                "CgroupnsMode": "private",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "Memory": MEMORY_BYTES,
                "MemorySwap": MEMORY_BYTES,
                "NanoCpus": 1000000000,
                "PidsLimit": 64,
                "Tmpfs": {"/scratch": SCRATCH_OPTIONS},
            }
            # Canonical JSON distinguishes true from 1 and false from 0.
            for key, expected in required.items():
                if json.dumps(host[key], sort_keys=True) != json.dumps(expected, sort_keys=True):
                    raise ValueError("Repair container confinement limit failed")
            if (
                any(
                    host.get(key) not in (None, [])
                    for key in (
                        "CapAdd",
                        "Devices",
                        "DeviceRequests",
                        "Binds",
                        "VolumesFrom",
                        "GroupAdd",
                    )
                )
                or host.get("Sysctls") not in (None, {})
                or host.get("CgroupParent", "") != ""
                or host["SecurityOpt"] not in (["no-new-privileges"], ["no-new-privileges:true"])
                or host["LogConfig"]["Type"] != "none"
            ):
                raise ValueError("Repair container confinement privilege failed")
            expected_limits = {
                "cpu": (30, 30),
                "fsize": (1048576, 1048576),
                "nofile": (128, 128),
                "nproc": (64, 64),
            }
            actual_limits = {
                entry["Name"]: (entry["Soft"], entry["Hard"]) for entry in host["Ulimits"]
            }
            if actual_limits != expected_limits or len(host["Ulimits"]) != len(expected_limits):
                raise ValueError("Repair container confinement process/file limits failed")
            mounts = evidence["Mounts"]
            if (
                not isinstance(mounts, list)
                or len(mounts) != 1
                or mounts[0]["Type"] != "bind"
                or mounts[0]["Destination"] != "/job"
                or mounts[0]["Source"] not in self._job_sources(Path(job))
                or mounts[0]["RW"] is not False
            ):
                raise ValueError("Repair container confinement mount failed")
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("Repair container confinement metadata invalid") from error

    def _command(self, executable: str, *arguments: str):
        # Fix a local socket endpoint; never inherit a remote DOCKER_HOST/context.
        endpoint = (
            "npipe:////./pipe/dockerDesktopLinuxEngine"
            if os.name == "nt"
            else "unix:///var/run/docker.sock"
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        return bounded_command(
            [executable, "--host", endpoint, *arguments],
            environment=environment,
            timeout=10,
            output_limit=65536,
        )

    def _read(self, executable: str, *arguments: str) -> object:
        result = self._command(executable, *arguments)
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("Sandbox read admission failed")
        return json.loads(result.stdout)

    def probe(self) -> SandboxProbe:
        """Read local runtime/image evidence without pulling, creating or starting anything."""

        def receipt(reason):
            return SandboxProbe(
                reason == "PREREQUISITES_PRESENT", reason, self.image_id, self.suite_sha256
            )

        executable = shutil.which("docker")
        if executable is None:
            return receipt("DOCKER_UNAVAILABLE")
        try:
            engine = self._read(executable, "info", "--format", "{{json .}}")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            return receipt("ENGINE_UNAVAILABLE")
        try:
            self.require_engine(engine)
        except ValueError:
            return receipt("ENGINE_UNSUPPORTED")
        try:
            image = self._read(
                executable, "image", "inspect", self.image_id, "--format", "{{json .}}"
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            return receipt("IMAGE_UNAVAILABLE")
        if (
            not isinstance(image, dict)
            or image.get("Id") != self.image_id
            or image.get("Os") != "linux"
        ):
            return receipt("IMAGE_UNSUPPORTED")
        return receipt("PREREQUISITES_PRESENT")
