"""Bounded transport for trusted parent-side commands, never a candidate sandbox."""

import math
import subprocess
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def bounded_command(
    arguments: list[str],
    *,
    environment: dict[str, str],
    timeout: float,
    output_limit: int,
) -> CommandResult:
    """Drain both binary pipes with a shared cap; reap the owned command on every exit.

    The caller owns executable/arguments/environment. Container lifetime is separate:
    killing a Docker CLI does not terminate its container. No process-tree isolation
    or target execution authority is provided by this transport.
    """
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 60
        or type(output_limit) is not int
        or not 1 <= output_limit <= 1048576
    ):
        raise ValueError("Invalid command transport bounds")
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        close_fds=True,
        bufsize=0,
    )
    lock, exceeded, read_failed = Lock(), Event(), Event()
    buffers = [bytearray(), bytearray()]
    done = [Event(), Event()]
    total = 0

    def drain(pipe, index):
        nonlocal total
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = max(0, output_limit - total)
                    buffers[index].extend(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        exceeded.set()
                        return
        except OSError:
            read_failed.set()
        finally:
            pipe.close()
            done[index].set()

    threads = [
        Thread(target=drain, args=(pipe, index), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    try:
        for thread in threads:
            thread.start()
        while True:
            # Once both readers finish their flags are final. Check those flags
            # after observing completion so a last-chunk overflow cannot pass.
            settled = process.poll() is not None and all(event.is_set() for event in done)
            if exceeded.is_set():
                raise ValueError("Command output limit exceeded")
            if read_failed.is_set():
                raise ValueError("Command output read failed")
            if time.monotonic() >= deadline:
                raise TimeoutError("Command deadline exceeded")
            if settled:
                return CommandResult(process.returncode, bytes(buffers[0]), bytes(buffers[1]))
            exceeded.wait(min(0.01, max(0, deadline - time.monotonic())))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=2)
        for pipe, thread in zip((process.stdout, process.stderr), threads, strict=True):
            if thread.ident is None:
                pipe.close()
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("Owned command pipes did not close after termination")
