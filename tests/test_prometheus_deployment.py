"""Actual pinned Prometheus parses and ingests original protected paper metrics."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.paper_telemetry import PaperTelemetry
from tests.test_paper_telemetry import NOW, payload, run_order

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra/observability/prometheus.json"


@pytest.fixture
def native_tools():
    value = os.getenv("AI_QUANT_NATIVE_PROMETHEUS_DIR")
    if not value:
        pytest.skip("Explicit pinned native Prometheus directory required")
    directory = Path(value).resolve()
    manifest = json.loads((ROOT / "infra/observability/prometheus-release.json").read_text())
    assert manifest["version"] == "3.15.0" and manifest["platform"] == "windows_amd64"
    for item in manifest["executables"]:
        binary = directory / item["name"]
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == item["sha256"]
        result = subprocess.run([str(binary), "--version"], capture_output=True, timeout=30)
        assert result.returncode == 0 and b"3.15.0" in result.stdout + result.stderr
    return directory


def test_actual_promtool_checks_original_sdk_metrics_and_private_scrape_config(
    native_tools, tmp_path
):
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    try:
        run_order(telemetry)
        result = subprocess.run(
            [str(native_tools / "promtool.exe"), "check", "metrics"],
            input=telemetry.metrics_text().encode(),
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        config = json.loads(CONFIG.read_text())
        assert config["scrape_configs"][0]["static_configs"] == [{"targets": ["127.0.0.1:8000"]}]
        assert config["scrape_configs"][0]["metrics_path"] == "/api/v1/observability/metrics"
        assert config["scrape_configs"][0]["http_headers"] == {
            "X-Operator-Token": {"files": ["operator-token"]}
        }
        assert config["scrape_configs"][0]["follow_redirects"] is False
        assert config["scrape_configs"][0]["label_limit"] == 7
        assert not config.get("remote_write") and not config.get("alerting")
        path = tmp_path / "prometheus.json"
        path.write_text(json.dumps(config))
        shutil.copyfile(
            CONFIG.parent / "paper-http-alerts.json", tmp_path / "paper-http-alerts.json"
        )
        (tmp_path / "operator-token").write_text("fixture-monitor-token")
        result = subprocess.run(
            [str(native_tools / "promtool.exe"), "check", "config", str(path)],
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    finally:
        telemetry.close()


def test_actual_prometheus_ingests_sdk_buckets_and_reports_revoked_scrape_without_retrying_orders(
    native_tools, tmp_path
):
    config = json.loads(CONFIG.read_text())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    app = create_app(operator_token="fixture-monitor-token", clock=lambda: NOW, telemetry=telemetry)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        prom_port = reservation.getsockname()[1]
    config["global"] = {
        "scrape_interval": "1s",
        "scrape_timeout": "500ms",
        "evaluation_interval": "1s",
    }
    config["scrape_configs"][0]["static_configs"] = [{"targets": [f"127.0.0.1:{port}"]}]
    config_path = tmp_path / "prometheus.json"
    config_path.write_text(json.dumps(config))
    shutil.copyfile(CONFIG.parent / "paper-http-alerts.json", tmp_path / "paper-http-alerts.json")
    token_file = tmp_path / "operator-token"
    token_file.write_text("fixture-monitor-token")
    process = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and thread.is_alive()
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as paper:
            headers = {
                "X-Operator-Token": "fixture-monitor-token",
                "Idempotency-Key": "fixture-monitor-order",
            }
            paper_base = f"http://127.0.0.1:{port}"
            assert (
                paper.post(
                    paper_base + "/api/v1/paper/quotes",
                    headers=headers,
                    json={
                        "instrument_id": "EURUSD",
                        "bid": "1.1000",
                        "ask": "1.1001",
                        "observed_at": NOW.isoformat(),
                        "world_state_version": "private-world",
                    },
                ).status_code
                == 200
            )
            accepted = paper.post(
                paper_base + "/api/v1/paper/intents", headers=headers, json=payload()
            )
            assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
            replay = paper.post(
                paper_base + "/api/v1/paper/intents", headers=headers, json=payload()
            )
            assert replay.status_code == 200 and replay.json() == accepted.json()
        with (tmp_path / "prometheus.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    str(native_tools / "prometheus.exe"),
                    "--config.file=" + str(config_path),
                    "--storage.tsdb.path=" + str(tmp_path / "tsdb"),
                    "--storage.tsdb.retention.time=3d",
                    "--storage.tsdb.retention.size=128MB",
                    f"--web.listen-address=127.0.0.1:{prom_port}",
                    "--log.level=warn",
                ],
                cwd=tmp_path,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            base = f"http://127.0.0.1:{prom_port}"
            with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:

                def query(expression, expected):
                    deadline = time.monotonic() + 30
                    while True:
                        assert process.poll() is None, "Original Prometheus exited"
                        try:
                            response = client.get(
                                base + "/api/v1/query", params={"query": expression}
                            )
                            rows = response.json()["data"]["result"]
                            if len(rows) == 1 and float(rows[0]["value"][1]) == expected:
                                return rows
                            if expression == 'up{job="paper_http"}' and expected == 1 and rows:
                                targets = client.get(base + "/api/v1/targets").json()["data"][
                                    "activeTargets"
                                ]
                                if targets and targets[0]["lastError"]:
                                    (tmp_path / "failed-target.json").write_text(
                                        json.dumps(targets, indent=2)
                                    )
                                    raise AssertionError(targets[0]["lastError"])
                        except (httpx.HTTPError, KeyError, ValueError):
                            pass
                        assert time.monotonic() < deadline, "Original query observation deadline"
                        time.sleep(0.1)

                query('up{job="paper_http"}', 1)
                selector = 'method="POST",route="/api/v1/paper/intents",status_class="2xx",job="paper_http"'
                query("ai_quant_paper_http_duration_seconds_count{" + selector + "}", 2)
                query("ai_quant_paper_http_duration_seconds_bucket{" + selector + ',le="+Inf"}', 2)
                assert client.get(base + "/api/v1/status/config").status_code == 200
                assert (
                    "fixture-monitor-token" not in client.get(base + "/api/v1/status/config").text
                )
                assert client.post(base + "/-/reload").status_code == 403
                token_file.write_text("revoked-fixture-token")
                query('up{job="paper_http"}', 0)
                deadline = time.monotonic() + 10
                while True:
                    assert process.poll() is None
                    response = client.get(base + "/api/v1/rules", params={"type": "alert"})
                    assert response.status_code == 200
                    rules = response.json()["data"]["groups"][0]["rules"]
                    observed = {rule["name"]: rule for rule in rules}
                    if observed["PaperHttpScrapeFailed"]["state"] == "pending":
                        assert observed["PaperHttpScrapeMissing"]["state"] == "inactive"
                        assert all(
                            rule["health"] == "ok" and rule["duration"] == 120 for rule in rules
                        )
                        (tmp_path / "native-alert-observation.json").write_text(
                            json.dumps(response.json())
                        )
                        break
                    assert time.monotonic() < deadline, "Original alert evaluation deadline"
                    time.sleep(0.1)
                targets = client.get(base + "/api/v1/targets").json()["data"]["activeTargets"]
                assert len(targets) == 1 and targets[0]["health"] == "down"
                assert "401" in targets[0]["lastError"]
                query(
                    "max_over_time(ai_quant_paper_http_duration_seconds_count{"
                    + selector
                    + "}[5m])",
                    2,
                )
                # A rejected scrape cannot submit/replay an order or erase retained samples.
                with httpx.Client(timeout=2, trust_env=False) as paper:
                    assert (
                        len(paper.get(f"http://127.0.0.1:{port}/api/v1/orders").json()["orders"])
                        == 1
                    )
                    assert (
                        paper.post(
                            f"http://127.0.0.1:{port}/api/v1/control/halt",
                            headers={
                                "X-Operator-Token": "fixture-monitor-token",
                                "Idempotency-Key": "fixture-monitor-halt",
                            },
                            json={"reason": "fixture monitor failure"},
                        ).status_code
                        == 200
                    )
                    assert (
                        len(paper.get(f"http://127.0.0.1:{port}/api/v1/orders").json()["orders"])
                        == 1
                    )
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            assert process.poll() is not None
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
        listener.close()
        telemetry.close()
