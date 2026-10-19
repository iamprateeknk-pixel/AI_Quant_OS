"""Actual built Mission Control and paper API under the same owned supervisor."""

import json
import os
import secrets
import socket
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


def _available_local_ports(count):
    listeners = []
    try:
        while len(listeners) < count:
            port = 12000 + secrets.randbelow(18000)
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                listener.close()
                continue
            listeners.append(listener)
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


@pytest.mark.skipif(
    os.getenv("AI_QUANT_REQUIRE_LOCAL_WEB") != "1", reason="Opt-in built frontend integration"
)
def test_real_ui_api_authentication_persistence_and_shutdown(tmp_path, monkeypatch):
    from ai_quant_os.local_runtime import LocalSettings, local_application

    ports = _available_local_ports(2)
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("AI_QUANT_OPERATOR_TOKEN", token)
    monkeypatch.setenv("AI_QUANT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_REVIEW_API_KEY", "fixture-disabled-provider")
    settings = LocalSettings(Path.cwd(), tmp_path, *ports)
    opener = build_opener(ProxyHandler({}))

    def request(path, *, method="GET", payload=None, authenticated=False):
        headers = {"Content-Type": "application/json", "Idempotency-Key": "local-startup-test"}
        if authenticated:
            headers["X-Operator-Token"] = token
        data = None if payload is None else json.dumps(payload).encode()
        with opener.open(
            Request(
                f"http://127.0.0.1:{ports[0]}" + path, data=data, headers=headers, method=method
            ),
            timeout=3,
        ) as response:
            return json.loads(response.read())

    with local_application(settings) as first:
        assert request("/api/v1/system/status")["mode"] == "PAPER"
        providers = request("/api/v1/decisions/provider")
        assert providers["configured"] is False
        assert providers["deep_review_configured"] is False
        with opener.open(f"http://127.0.0.1:{ports[1]}", timeout=5) as response:
            page = response.read()
        assert b"Mission Control" in page and b"PAPER" in page
        assert b"Control API unavailable" not in page
        assert token.encode() not in page and b"fixture-disabled-provider" not in page
        with pytest.raises(HTTPError) as denied:
            request(
                "/api/v1/control/no-new-risk",
                method="POST",
                payload={"reason": "local startup integration"},
            )
        assert denied.value.code == 401
        request(
            "/api/v1/control/no-new-risk",
            method="POST",
            payload={"reason": "local startup integration"},
            authenticated=True,
        )
        assert request("/api/v1/system/status")["safety_state"] == "NO_NEW_RISK"
    assert all(process.process.poll() is not None for process in first)
    with local_application(settings) as second:
        assert request("/api/v1/system/status")["safety_state"] == "NO_NEW_RISK"
    assert all(process.process.poll() is not None for process in second)
    for port in ports:
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))


@pytest.mark.skipif(
    os.getenv("AI_QUANT_REQUIRE_LOCAL_WEB") != "1", reason="Opt-in built frontend integration"
)
def test_opt_in_telemetry_supervises_local_collector_and_retains_request_trace(
    tmp_path, monkeypatch
):
    from ai_quant_os.local_runtime import LocalSettings, local_application

    root = Path.cwd()
    collector = root / "data-local/tools/otelcol-contrib-0.162.0/otelcol-contrib.exe"
    if not collector.is_file():
        pytest.skip("Pinned local OpenTelemetry Collector is not installed")
    ports = _available_local_ports(3)
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("AI_QUANT_OPERATOR_TOKEN", token)
    monkeypatch.setenv("AI_QUANT_OTEL_ENABLED", "1")
    monkeypatch.setenv("AI_QUANT_OTEL_COLLECTOR_MANAGED", "1")
    monkeypatch.setenv("AI_QUANT_OTEL_ENDPOINT", f"http://127.0.0.1:{ports[2]}/v1/traces")
    settings = LocalSettings(root, tmp_path / "state", ports[0], ports[1])
    opener = build_opener(ProxyHandler({}))
    trace_file = settings.state / "observability/paper-traces.jsonl"
    with local_application(settings) as children:
        assert len(children) == 3
        with opener.open(
            f"http://127.0.0.1:{ports[0]}/api/v1/system/status", timeout=5
        ) as response:
            assert json.loads(response.read())["mode"] == "PAPER"
            trace_id = response.headers.get("X-Paper-Trace-Id")
        assert trace_id and len(trace_id) == 32
        deadline = time.monotonic() + 10
        retained = ""
        while time.monotonic() < deadline:
            if trace_file.exists():
                retained = trace_file.read_text(encoding="utf-8")
                if trace_id.lower() in retained.lower():
                    break
            time.sleep(0.05)
        assert trace_file.is_file() and trace_id.lower() in retained.lower()
    assert all(child.process.poll() is not None for child in children)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", ports[2]))


@pytest.mark.skipif(
    os.getenv("AI_QUANT_REQUIRE_LOCAL_WEB") != "1", reason="Opt-in Node startup failure integration"
)
def test_frontend_exit_cleans_up_already_ready_api(tmp_path, monkeypatch):
    from ai_quant_os import local_runtime

    checkout = tmp_path / "checkout"
    web = checkout / "apps/web"
    binary = web / "node_modules/next/dist/bin/next"
    binary.parent.mkdir(parents=True)
    binary.write_text("process.exit(7)", encoding="utf-8")
    (web / ".next").mkdir()
    (web / ".next/BUILD_ID").write_text("trusted-exit-fixture", encoding="utf-8")
    ports = _available_local_ports(2)
    original = local_runtime.OwnedProcess
    created = []

    def observe(*args, **kwargs):
        child = original(*args, **kwargs)
        created.append(child)
        return child

    monkeypatch.setattr(local_runtime, "OwnedProcess", observe)
    settings = local_runtime.LocalSettings(checkout, tmp_path / "state", *ports)
    with pytest.raises(RuntimeError, match="exited"):
        with local_runtime.local_application(settings):
            pytest.fail("Exited frontend must not become ready")
    assert len(created) == 2
    assert all(child.process.poll() is not None for child in created)
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", ports[0]))
