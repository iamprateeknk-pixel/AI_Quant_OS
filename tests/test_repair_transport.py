"""Only trusted fixture programs run here; candidate source is never executed."""

import importlib
import json
import os
import sys
import time

import pytest


def runner():
    assert importlib.util.find_spec("ai_quant_os.repair_transport") is not None
    return importlib.import_module("ai_quant_os.repair_transport").bounded_command


def environment():
    return {
        key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR"}
    }


def test_exact_binary_streams_are_preserved_and_ambient_secrets_are_not_inherited(monkeypatch):
    monkeypatch.setenv("AI_QUANT_TRANSPORT_SECRET", "DO_NOT_INHERIT")
    result = runner()(
        [
            sys.executable,
            "-I",
            "-c",
            "import os,sys;sys.stdout.buffer.write(b'\\x00\\xff');sys.stderr.write(str('AI_QUANT_TRANSPORT_SECRET' in os.environ));sys.exit(7)",
        ],
        environment=environment(),
        timeout=5,
        output_limit=64,
    )
    assert result.returncode == 7
    assert result.stdout == b"\x00\xff"
    assert result.stderr == b"False"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_infinite_output_is_stopped_before_deadline_without_retaining_excess(stream):
    started = time.monotonic()
    with pytest.raises(ValueError, match="output"):
        runner()(
            [
                sys.executable,
                "-I",
                "-c",
                f"import sys;\nwhile True: sys.{stream}.buffer.write(b'x'*4096);sys.{stream}.flush()",
            ],
            environment=environment(),
            timeout=5,
            output_limit=8192,
        )
    assert time.monotonic() - started < 5


def test_combined_stream_limit_and_exact_boundary():
    script = "import sys;sys.stdout.buffer.write(b'a'*32);sys.stderr.buffer.write(b'b'*32)"
    result = runner()(
        [sys.executable, "-I", "-c", script], environment=environment(), timeout=5, output_limit=64
    )
    assert result.stdout + result.stderr == b"a" * 32 + b"b" * 32
    with pytest.raises(ValueError, match="output"):
        runner()(
            [sys.executable, "-I", "-c", script],
            environment=environment(),
            timeout=5,
            output_limit=63,
        )


def test_timeout_reaps_own_child_before_return(tmp_path):
    marker = tmp_path / "late-marker"
    script = (
        "import time,pathlib;time.sleep(1);pathlib.Path("
        + repr(str(marker))
        + ").write_text('orphan')"
    )
    with pytest.raises(TimeoutError, match="deadline"):
        runner()(
            [sys.executable, "-I", "-c", script],
            environment=environment(),
            timeout=0.15,
            output_limit=64,
        )
    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.parametrize(
    "timeout,limit",
    [(0, 64), (float("nan"), 64), (float("inf"), 64), (61, 64), (5, 0), (5, 1048577), (5, True)],
)
def test_invalid_limits_do_not_launch_program(tmp_path, timeout, limit):
    marker = tmp_path / "should-not-start"
    with pytest.raises(ValueError):
        runner()(
            [
                sys.executable,
                "-I",
                "-c",
                "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()",
            ],
            environment=environment(),
            timeout=timeout,
            output_limit=limit,
        )
    assert not marker.exists()


def test_docker_reader_uses_fixed_local_endpoint_and_bounded_transport(monkeypatch):
    from ai_quant_os.repair_sandbox import DockerConfinement
    from ai_quant_os.repair_transport import CommandResult

    calls = []

    def transport(arguments, **options):
        calls.append((arguments, options))
        return CommandResult(0, json.dumps({"fixture": True}).encode(), b"")

    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted:2375")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "DO_NOT_INHERIT")
    monkeypatch.setattr("ai_quant_os.repair_sandbox.bounded_command", transport)
    policy = DockerConfinement("sha256:" + "a" * 64, "b" * 64)
    assert policy._read("owned-docker", "info") == {"fixture": True}
    arguments, options = calls[0]
    assert arguments[0:2] == ["owned-docker", "--host"]
    assert "tcp://untrusted:2375" not in arguments
    assert "DOCKER_HOST" not in options["environment"]
    assert "AWS_SECRET_ACCESS_KEY" not in options["environment"]
    assert options["output_limit"] == 65536
    assert options["timeout"] == 10


def test_binary_input_arrives_exactly_while_both_output_streams_are_drained():
    from hashlib import sha256

    payload = bytes(range(256)) * 16384
    script = (
        "import sys,hashlib;sys.stdout.buffer.write(b'a'*32768);sys.stdout.flush();"
        "sys.stderr.buffer.write(b'b'*32768);sys.stderr.flush();"
        "sys.stdout.buffer.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest().encode()+b'\\n')"
    )
    result = runner()(
        [sys.executable, "-I", "-c", script],
        environment=environment(),
        timeout=5,
        output_limit=65601,
        input_bytes=payload,
    )
    assert result.returncode == 0
    assert result.stdout == b"a" * 32768 + sha256(payload).hexdigest().encode() + b"\n"
    assert result.stderr == b"b" * 32768


def test_blocked_input_writer_obeys_deadline_and_reaps_owned_process(tmp_path):
    marker = tmp_path / "input-orphan"
    script = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").touch()"
    with pytest.raises(TimeoutError, match="deadline"):
        runner()(
            [sys.executable, "-I", "-c", script],
            environment=environment(),
            timeout=0.15,
            output_limit=64,
            input_bytes=b"x" * 4194304,
        )
    time.sleep(1.1)
    assert not marker.exists()


def test_incomplete_input_delivery_is_not_a_successful_command():
    with pytest.raises(ValueError, match="input"):
        runner()(
            [sys.executable, "-I", "-c", "import sys;sys.stdin.close()"],
            environment=environment(),
            timeout=5,
            output_limit=64,
            input_bytes=b"x" * 4194304,
        )


@pytest.mark.parametrize("payload", ["text", bytearray(b"mutable"), True])
def test_invalid_input_does_not_launch_program(tmp_path, payload):
    marker = tmp_path / "invalid-input"
    with pytest.raises(ValueError, match="input"):
        runner()(
            [
                sys.executable,
                "-I",
                "-c",
                "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()",
            ],
            environment=environment(),
            timeout=5,
            output_limit=64,
            input_bytes=payload,
        )
    assert not marker.exists()
