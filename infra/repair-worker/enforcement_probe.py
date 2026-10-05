"""Fixed adversarial operations; admit neither proposed code nor market data."""

import errno
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

GUARD_SHA256 = "59ac6c56aeaf0c062d8f732b77978a11b6163d16fc326c0a3e8ad8014625aa85"
SCENARIOS = ("PRIVILEGE_AND_NOEXEC", "MEMORY", "CPU", "PROCESSES", "FILES")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate enforcement probe field")
        value[key] = item
    return value


def decode_request(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 1024:
        raise ValueError("Enforcement probe input bound")
    value = json.loads(raw, object_pairs_hook=_object)
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "purpose", "scenario"}
        or value["schema_version"] != "1.0"
        or value["purpose"] != "WORKER_ENFORCEMENT_PROBE"
        or value["scenario"] not in SCENARIOS
    ):
        raise ValueError("Fixed enforcement probe scenario required")
    return value["scenario"]


def _guard():
    if sys.platform != "linux" or sys.implementation.name != "cpython":
        raise ValueError("Linux enforcement probe required")
    descriptor = os.open("/job/runtime_guard.py", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(32769)
    if sha256(raw).hexdigest() != GUARD_SHA256:
        raise ValueError("Trusted enforcement guard changed")
    spec = importlib.util.spec_from_file_location("trusted_runtime_guard", "/job/runtime_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.require_candidate_test_runtime()


def _denied(operation, expected):
    try:
        operation()
    except OSError as error:
        if error.errno != expected:
            raise ValueError("Unexpected enforcement error") from error
    else:
        raise ValueError("Protected operation unexpectedly succeeded")


def _privilege_and_noexec():
    _denied(lambda: os.setuid(0), errno.EPERM)
    _denied(lambda: os.setgid(0), errno.EPERM)
    _denied(lambda: os.setgroups([]), errno.EPERM)
    _denied(
        lambda: socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP), errno.EPERM
    )
    payload = Path("/scratch/controlled-noexec.sh")
    payload.write_bytes(b"#!/bin/sh\nexit 0\n")
    payload.chmod(0o755)
    _denied(lambda: subprocess.run([str(payload)], timeout=3, check=True), errno.EACCES)
    return {
        "setuid_denied": True,
        "setgid_denied": True,
        "supplementary_groups_denied": True,
        "raw_socket_denied": True,
        "scratch_direct_execution_denied": True,
    }


def _processes():
    read_end, write_end = os.pipe()
    children = []
    denied = False
    try:
        for _ in range(70):
            try:
                pid = os.fork()
            except OSError as error:
                if error.errno != errno.EAGAIN:
                    raise
                denied = True
                break
            if pid == 0:
                os.close(write_end)
                os.read(read_end, 1)
                os._exit(0)
            children.append(pid)
        current = int(Path("/sys/fs/cgroup/pids.current").read_text().strip())
        if not denied or not 1 <= len(children) <= 63 or not 1 <= current <= 64:
            raise ValueError("Process creation limit not enforced")
        return {
            "process_creation_denied": True,
            "created_children": len(children),
            "observed_pids": current,
        }
    finally:
        os.close(write_end)
        os.close(read_end)
        for pid in children:
            _, status = os.waitpid(pid, 0)
            if status != 0:
                raise ValueError("Controlled probe child did not exit normally")


def _files():
    import signal

    # Observe the write error directly; ignoring SIGXFSZ does not relax RLIMIT_FSIZE.
    prior = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    descriptor = os.open(
        "/scratch/controlled-size.bin", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        if os.write(descriptor, b"x" * 1048576) != 1048576:
            raise ValueError("Controlled initial file write failed")
        _denied(lambda: os.write(descriptor, b"x"), errno.EFBIG)
        size = os.fstat(descriptor).st_size
        if size != 1048576:
            raise ValueError("File size limit changed")
    finally:
        os.close(descriptor)
        signal.signal(signal.SIGXFSZ, prior)
    descriptors = []
    denied = False
    try:
        for _ in range(130):
            try:
                descriptors.append(os.open("/dev/null", os.O_RDONLY))
            except OSError as error:
                if error.errno != errno.EMFILE:
                    raise
                denied = True
                break
        if not denied or not 1 <= len(descriptors) <= 125:
            raise ValueError("Descriptor ceiling not enforced")
        return {
            "file_size_denied": True,
            "file_bytes": size,
            "descriptor_limit_denied": True,
            "opened_descriptors": len(descriptors),
        }
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def run():
    _guard()
    descriptor = os.open("/job/request.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        scenario = decode_request(stream.read(1025))
    if scenario == "MEMORY":
        # Touch a fixed allocation above the existing 512 MiB cgroup ceiling.
        allocated = bytearray(1073741824)
        raise ValueError("Memory limit failed: " + str(len(allocated)))
    if scenario == "CPU":
        while True:
            pass
    observations = {
        "PRIVILEGE_AND_NOEXEC": _privilege_and_noexec,
        "PROCESSES": _processes,
        "FILES": _files,
    }[scenario]()
    return {
        "schema_version": "1.0",
        "scenario": scenario,
        "status": "CONTROLLED_ENFORCEMENT_OBSERVED",
        "observations": observations,
        "proposed_source_executed": False,
        "isolation_verified": False,
        "promotion_allowed": False,
    }


def main():
    try:
        value = run()
        sys.stdout.write(json.dumps(value, separators=(",", ":")))
        return 0
    except Exception:
        sys.stderr.write("WORKER_ENFORCEMENT_PROBE_REFUSED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
