"""Trusted local service startup; no repair/candidate code is executed."""

import importlib
import json
import os
import socket
import sys
import time
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def runtime():
    assert importlib.util.find_spec("ai_quant_os.local_runtime"), "Missing local app supervisor"
    return importlib.import_module("ai_quant_os.local_runtime")


def processes():
    assert importlib.util.find_spec("ai_quant_os.local_processes"), "Missing owned service lifetime"
    return importlib.import_module("ai_quant_os.local_processes")


def test_busy_port_refuses_without_stopping_existing_listener(tmp_path):
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        port = existing.getsockname()[1]
        with pytest.raises(ValueError, match="port"):
            runtime().LocalSettings(Path.cwd(), tmp_path, port, port + 1).validate()
        assert existing.fileno() >= 0
        assert not (tmp_path / "paper.sqlite3").exists()


@pytest.mark.parametrize("ports", [(0, 3000), (8000, 8000), (True, 3000), (8000, 65536)])
def test_invalid_port_configuration_refuses(tmp_path, ports):
    with pytest.raises(ValueError):
        runtime().LocalSettings(Path.cwd(), tmp_path, *ports).validate()


def test_child_environments_keep_operator_secret_only_on_backend(tmp_path):
    settings = runtime().LocalSettings(Path.cwd(), tmp_path, 8000, 3000)
    backend, frontend = settings.environments(
        {
            "PATH": "trusted",
            "AI_QUANT_OPERATOR_TOKEN": "fixture-operator-secret",
            "OPENAI_API_KEY": "fixture-provider-secret",
            "AI_QUANT_REVIEW_ENABLED": "true",
            "AI_QUANT_HEALTH_WATCHDOG_ENABLED": "1",
            "NODE_OPTIONS": "--require unwanted.js",
            "AI_QUANT_API_URL": "https://external.invalid",
            "PYTHONPATH": "unwanted",
        }
    )
    assert backend["AI_QUANT_OPERATOR_TOKEN"] == "fixture-operator-secret"
    assert "AI_QUANT_OPERATOR_TOKEN" not in frontend
    assert "OPENAI_API_KEY" not in backend and "OPENAI_API_KEY" not in frontend
    assert "NODE_OPTIONS" not in frontend and "PYTHONPATH" not in backend
    assert backend["AI_QUANT_REVIEW_ENABLED"] == "false"
    assert backend["AI_QUANT_HEALTH_WATCHDOG_ENABLED"] == "0"
    assert frontend["AI_QUANT_API_URL"] == "http://127.0.0.1:8000"
    assert backend["AI_QUANT_PAPER_DB"] == str(tmp_path.resolve() / "paper.sqlite3")


def wait_file(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.03)
    raise AssertionError("Trusted process fixture did not become ready")


def test_owned_tree_stops_descendant_and_reaps_direct_child(tmp_path):
    heartbeat = tmp_path / "heartbeat.txt"
    worker = (
        "import pathlib,time; p=pathlib.Path(" + repr(str(heartbeat)) + "); "
        "\nfor count in range(300): p.write_text(str(count)); time.sleep(.03)"
    )
    parent = (
        "import subprocess,sys,time; sys.stdin.readline(); "
        f"subprocess.Popen([sys.executable,'-c',{worker!r}]); time.sleep(30)"
    )
    owned = processes().OwnedProcess(
        [sys.executable, "-c", parent], cwd=tmp_path, environment=os.environ.copy()
    )
    try:
        owned.process.stdin.write(b"start\n")
        owned.process.stdin.flush()
        wait_file(heartbeat)
    finally:
        owned.close()
    assert owned.process.poll() is not None
    prior = heartbeat.read_bytes()
    time.sleep(0.2)
    assert heartbeat.read_bytes() == prior
    owned.close()


def test_premature_child_exit_never_reports_readiness(tmp_path):
    owned = processes().OwnedProcess(
        [sys.executable, "-c", "raise SystemExit(7)"], cwd=tmp_path, environment=os.environ.copy()
    )
    try:
        owned.process.wait(timeout=5)
        with pytest.raises(RuntimeError):
            runtime().wait_ready(owned, "http://127.0.0.1:1/", lambda body: True, timeout=1)
    finally:
        owned.close()


def test_missing_frontend_build_refuses_without_creating_storage(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    with pytest.raises(ValueError):
        runtime().LocalSettings(root, tmp_path / "state", 8000, 3000).validate()
    assert not (tmp_path / "state").exists()


def test_slow_readiness_headers_cannot_extend_total_deadline(tmp_path):
    class SlowHeaders(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                self.wfile.flush()
                for _ in range(40):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b"\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHeaders)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    owned = processes().OwnedProcess(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        cwd=tmp_path,
        environment=os.environ.copy(),
    )
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="deadline"):
            runtime().wait_ready(
                owned, f"http://127.0.0.1:{server.server_port}", lambda body: False, timeout=0.3
            )
        elapsed = time.monotonic() - started
    finally:
        owned.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert elapsed < 1.3


def test_private_api_wrapper_stops_on_parent_pipe_eof(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment, _ = (
        runtime().LocalSettings(Path.cwd(), tmp_path, port, port + 1).environments(dict(os.environ))
    )
    environment["AI_QUANT_LOCAL_INSTANCE"] = "a" * 32
    owned = processes().OwnedProcess(
        [sys.executable, "-I", "-m", "ai_quant_os.local_api", str(port)],
        cwd=Path.cwd(),
        environment=environment,
    )
    try:
        runtime().wait_ready(
            owned,
            f"http://127.0.0.1:{port}/_local/ready",
            lambda body: json.loads(body) == {"instance": "a" * 32, "mode": "PAPER"},
            timeout=15,
        )
        owned.process.stdin.close()
        try:
            result = owned.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pytest.fail("Private API ignored parent pipe EOF")
        assert result == 0
    finally:
        owned.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel job lifetime")
def test_supervisor_death_closes_job_and_stops_owned_tree(tmp_path):
    heartbeat = tmp_path / "orphan-heartbeat.txt"
    worker = (
        "import pathlib,time; p=pathlib.Path("
        + repr(str(heartbeat))
        + ");\nfor count in range(200): p.write_text(str(count)); time.sleep(.03)"
    )
    child = (
        "import subprocess,sys,time; sys.stdin.readline(); "
        + f"subprocess.Popen([sys.executable,'-c',{worker!r}]); time.sleep(20)"
    )
    supervisor = (
        "from pathlib import Path; import os,sys,time; from ai_quant_os.local_processes import OwnedProcess; "
        + f"p=OwnedProcess([sys.executable,'-c',{child!r}],cwd=Path({str(tmp_path)!r}),environment=dict(os.environ)); "
        + "p.process.stdin.write(b'start\\n'); p.process.stdin.flush(); time.sleep(20)"
    )
    parent = subprocess.Popen(
        [sys._base_executable, "-c", supervisor],
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_file(heartbeat)
    finally:
        parent.kill()
        parent.wait(timeout=5)
    time.sleep(0.1)
    prior = heartbeat.read_bytes()
    time.sleep(0.2)
    assert heartbeat.read_bytes() == prior


def test_two_api_launches_cannot_share_one_paper_state_root(tmp_path):
    ports = []
    with socket.socket() as first_port, socket.socket() as second_port:
        first_port.bind(("127.0.0.1", 0))
        second_port.bind(("127.0.0.1", 0))
        ports = [first_port.getsockname()[1], second_port.getsockname()[1]]
    environment, _ = (
        runtime().LocalSettings(Path.cwd(), tmp_path, *ports).environments(dict(os.environ))
    )
    environment["AI_QUANT_LOCAL_INSTANCE"] = "b" * 32
    first = processes().OwnedProcess(
        [sys.executable, "-I", "-m", "ai_quant_os.local_api", str(ports[0])],
        cwd=Path.cwd(),
        environment=environment,
    )
    second = None
    try:
        runtime().wait_ready(
            first,
            f"http://127.0.0.1:{ports[0]}/_local/ready",
            lambda body: json.loads(body).get("instance") == "b" * 32,
            timeout=15,
        )
        second = processes().OwnedProcess(
            [sys.executable, "-I", "-m", "ai_quant_os.local_api", str(ports[1])],
            cwd=Path.cwd(),
            environment=environment,
        )
        with pytest.raises(RuntimeError):
            runtime().wait_ready(
                second,
                f"http://127.0.0.1:{ports[1]}/_local/ready",
                lambda body: json.loads(body).get("instance") == "b" * 32,
                timeout=10,
            )
        assert first.process.poll() is None
    finally:
        if second:
            second.close()
        first.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended process assignment")
def test_windows_service_cannot_run_before_job_assignment(tmp_path, monkeypatch):
    module = processes()
    original = module._WindowsJob.assign
    marker = tmp_path / "started.txt"
    observed = []

    def delayed_assign(job, process):
        time.sleep(0.3)
        observed.append(marker.exists())
        original(job, process)

    monkeypatch.setattr(module._WindowsJob, "assign", delayed_assign)
    owned = module.OwnedProcess(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started'); import time; time.sleep(20)",
        ],
        cwd=tmp_path,
        environment=dict(os.environ),
    )
    try:
        wait_file(marker)
    finally:
        owned.close()
    assert observed == [False]
