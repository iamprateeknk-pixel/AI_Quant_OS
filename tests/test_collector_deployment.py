"""Pinned native upstream collector proves file observations without granting authority."""

from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import time

import httpx
import pytest

from ai_quant_os.paper_telemetry import LocalOtlpExporter, PaperTelemetry

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra/observability/collector.json"
RELEASE = ROOT / "infra/observability/collector-release.json"


def test_collector_configuration_is_local_bounded_and_has_no_remote_export():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["receivers"] == {
        "otlp": {
            "protocols": {"http": {"endpoint": "127.0.0.1:4318", "max_request_body_size": 262144}}
        }
    }
    assert config["exporters"] == {
        "file/paper": {
            "path": "${env:AI_QUANT_COLLECTOR_TRACE_FILE}",
            "format": "json",
            "rotation": {"max_megabytes": 32, "max_days": 7, "max_backups": 3, "localtime": False},
        }
    }
    assert config["service"]["pipelines"] == {
        "traces": {
            "receivers": ["otlp"],
            "processors": ["memory_limiter"],
            "exporters": ["file/paper"],
        }
    }
    assert config["service"]["telemetry"]["metrics"]["level"] == "none"
    assert config["processors"]["memory_limiter"] == {
        "check_interval": "1s",
        "limit_mib": 128,
        "spike_limit_mib": 32,
    }


@pytest.fixture
def native_collector():
    value = os.getenv("AI_QUANT_NATIVE_COLLECTOR_EXE")
    if not value:
        pytest.skip("Pinned native collector acceptance requires explicit executable path")
    executable = Path(value).resolve()
    manifest = json.loads(RELEASE.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.162.0" and manifest["platform"] == "windows_amd64"
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == manifest["executable_sha256"]
    result = subprocess.run([str(executable), "--version"], capture_output=True, timeout=30)
    assert result.returncode == 0 and b"0.162.0" in result.stdout
    return executable


def test_native_collector_validates_configuration(native_collector, tmp_path):
    env = {**os.environ, "AI_QUANT_COLLECTOR_TRACE_FILE": str(tmp_path / "traces.jsonl")}
    result = subprocess.run(
        [str(native_collector), "validate", "--config=" + str(CONFIG)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_native_sdk_export_is_retained_after_owned_collector_termination(
    native_collector, tmp_path
):
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{port}"
    config_path = tmp_path / "collector.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "traces.jsonl"
    log_path = tmp_path / "collector.log"
    telemetry = None
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [str(native_collector), "--config=" + str(config_path)],
            env={**os.environ, "AI_QUANT_COLLECTOR_TRACE_FILE": str(output)},
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            endpoint = f"http://127.0.0.1:{port}/v1/traces"
            deadline = time.monotonic() + 30
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while True:
                    assert process.poll() is None, log_path.read_text(errors="replace")
                    try:
                        response = client.get(endpoint)
                        if response.status_code == 405:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "Original collector did not become ready"
                    time.sleep(0.05)
                oversize = client.post(
                    endpoint,
                    content=b"x" * 262145,
                    headers={"Content-Type": "application/x-protobuf"},
                )
                # This pinned receiver reports body-read limits as OTLP HTTP 400.
                assert oversize.status_code == 400
                assert b"http: request body too large" in oversize.content
            telemetry = PaperTelemetry(exporter=LocalOtlpExporter(endpoint))
            from test_paper_telemetry import run_order

            response = run_order(telemetry)
            assert response.json()["status"] == "ACCEPTED"
            assert telemetry.flush()
            assert telemetry.diagnostics()["acknowledged_spans"] == 7
            deadline = time.monotonic() + 10
            while not output.exists() or output.stat().st_size == 0:
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            before = output.read_bytes()
            spans = [
                span
                for line in before.splitlines()
                for resource in json.loads(line)["resourceSpans"]
                for scope in resource["scopeSpans"]
                for span in scope["spans"]
            ]
            accepted = [
                span for span in spans if span["traceId"] == response.headers["X-Paper-Trace-Id"]
            ]
            assert {span["name"] for span in accepted} == {
                "paper.http",
                "paper.market.snapshot",
                "paper.risk.evaluate",
                "paper.order.submit",
            }
            root_span = next(span for span in accepted if span["name"] == "paper.http")
            assert all(
                span["parentSpanId"] == root_span["spanId"]
                for span in accepted
                if span["name"] != "paper.http"
            )
            assert any(span["name"] == "paper.order.replay" for span in spans)
            assert all(
                secret not in before
                for secret in [
                    b"secret-intent",
                    b"secret-strategy",
                    b"private-key",
                    b"private-world",
                    b"private-evidence",
                ]
            )
            from fastapi.testclient import TestClient
            from ai_quant_os.api import create_app

            with TestClient(
                create_app(
                    operator_token="private-read-token",
                    telemetry=PaperTelemetry(),
                    collector_trace_file=output,
                )
            ) as client:
                lookup_path = "/api/v1/observability/traces/" + response.headers["X-Paper-Trace-Id"]
                assert client.get(lookup_path).status_code == 401
                lookup = client.get(lookup_path, headers={"X-Operator-Token": "private-read-token"})
                assert lookup.status_code == 200
                observation = lookup.json()
                assert observation["state"] == "RECORDED_SPANS" and len(observation["spans"]) == 4
                assert (
                    observation["delivery_verified"] is False
                    and observation["complete_trace"] is False
                )
                assert {span["span_id"] for span in observation["spans"]} == {
                    span["spanId"] for span in accepted
                }
                assert "private" not in lookup.text and "secret" not in lookup.text
            process.terminate()
            process.wait(timeout=30)
            assert process.poll() is not None
            outage = run_order(telemetry)
            assert outage.json()["status"] == "ACCEPTED"
            assert telemetry.flush()
            diagnostics = telemetry.diagnostics()
            assert diagnostics["acknowledged_spans"] == 7
            assert diagnostics["unconfirmed_spans"] == 7
            # A separate admission starts only after the original collector is terminal.
            replacement = subprocess.Popen(
                [str(native_collector), "--config=" + str(config_path)],
                env={**os.environ, "AI_QUANT_COLLECTOR_TRACE_FILE": str(output)},
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                deadline = time.monotonic() + 30
                with httpx.Client(timeout=0.5, trust_env=False) as client:
                    while True:
                        assert replacement.poll() is None
                        try:
                            if client.get(endpoint).status_code == 405:
                                break
                        except httpx.HTTPError:
                            pass
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                assert output.read_bytes() == before
                # Failed original exports are not retried or relabelled by a new collector.
                assert telemetry.diagnostics()["unconfirmed_spans"] == 7
            finally:
                if replacement.poll() is None:
                    replacement.terminate()
                replacement.wait(timeout=30)
        finally:
            if telemetry:
                telemetry.close()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=30)
        assert process.poll() is not None and output.read_bytes() == before
