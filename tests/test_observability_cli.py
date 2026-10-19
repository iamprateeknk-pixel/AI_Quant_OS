import json
from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn

from ai_quant_os.api import create_app
from ai_quant_os.observability_cli import RuntimeSettings, prepare_runtime, run
from ai_quant_os.paper_telemetry import PaperTelemetry
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_prepare_runtime_binds_only_loopback_and_keeps_credentials_out_of_child_environment(
    tmp_path: Path,
    monkeypatch,
):
    token = "synthetic-paper-token"
    password = "synthetic-grafana-password-A9!"
    monkeypatch.setenv("AI_QUANT_OPERATOR_TOKEN", token)
    monkeypatch.setenv("AI_QUANT_GRAFANA_ADMIN_PASSWORD", password)
    settings = RuntimeSettings(
        checkout=Path(__file__).resolve().parents[1],
        state=tmp_path / "persistent",
        api_port=8124,
        prometheus_port=9190,
        grafana_port=3190,
        operator_token=token,
        grafana_password=password,
    )
    runtime = prepare_runtime(settings, tmp_path / "run")

    config = json.loads(runtime.prometheus_config.read_text(encoding="utf-8"))
    scrape = config["scrape_configs"][0]
    assert scrape["static_configs"] == [{"targets": ["127.0.0.1:8124"]}]
    assert scrape["http_headers"] == {"X-Operator-Token": {"files": ["operator-token"]}}
    assert token not in runtime.prometheus_config.read_text(encoding="utf-8")
    assert (runtime.run_directory / "operator-token").read_text(encoding="utf-8") == token
    assert runtime.prometheus_arguments[-1] == "--web.listen-address=127.0.0.1:9190"

    datasource = (runtime.provisioning / "datasources/paper.yaml").read_text(encoding="utf-8")
    assert "http://127.0.0.1:9190" in datasource
    assert runtime.dashboards.is_dir()
    assert (runtime.run_directory / "operator-admin-password").read_text(
        encoding="utf-8"
    ) == password
    assert (runtime.run_directory / "operator-secret-key").stat().st_size >= 32

    for environment in (runtime.prometheus_environment, runtime.grafana_environment):
        assert token not in environment.values()
        assert password not in environment.values()
        assert "AI_QUANT_OPERATOR_TOKEN" not in environment
        assert "AI_QUANT_GRAFANA_ADMIN_PASSWORD" not in environment
    assert runtime.grafana_environment["GF_SERVER_HTTP_ADDR"] == "127.0.0.1"
    assert runtime.grafana_environment["GF_SERVER_HTTP_PORT"] == "3190"


def test_runtime_rejects_invalid_or_colliding_ports(tmp_path: Path):
    checkout = Path(__file__).resolve().parents[1]
    base = RuntimeSettings(
        checkout=checkout,
        state=tmp_path / "state",
        api_port=8000,
        prometheus_port=9090,
        grafana_port=3100,
        operator_token="token",
        grafana_password="password",
    )
    invalid_ports = (
        RuntimeSettings(**{**base.__dict__, "api_port": 9090}),
        RuntimeSettings(**{**base.__dict__, "prometheus_port": 3100}),
        RuntimeSettings(**{**base.__dict__, "grafana_port": 8000}),
        RuntimeSettings(**{**base.__dict__, "api_port": 0}),
    )
    for invalid in invalid_ports:
        with pytest.raises(ValueError):
            invalid.validate()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_supervisor_runs_pinned_stack_and_removes_temporary_credentials(
    tmp_path: Path, monkeypatch, capsys
):
    root = Path(__file__).resolve().parents[1]
    if not (root / "data-local/tools/prometheus-3.15.0/prometheus.exe").is_file():
        pytest.skip("Pinned local Prometheus distribution is not installed")
    if not (root / "data-local/tools/grafana-13.2.3/bin/grafana.exe").is_file():
        pytest.skip("Pinned local Grafana distribution is not installed")
    token = "fixture-local-observability-token"
    monkeypatch.setenv("AI_QUANT_OPERATOR_TOKEN", token)
    monkeypatch.setenv("AI_QUANT_GRAFANA_ADMIN_PASSWORD", "fixture-grafana-password-9031")
    telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
    ports = []
    while len(ports) < 3:
        port = _free_port()
        if port not in ports:
            ports.append(port)
    api_port, prom_port, grafana_port = ports
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(
                operator_token=token,
                paper_db_path=tmp_path / "paper.sqlite3",
                telemetry=telemetry,
            ),
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={
            "sockets": [
                _listener(api_port),
            ]
        },
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert server.started and thread.is_alive()
        assert (
            run(
                RuntimeSettings(
                    root,
                    tmp_path / "state",
                    api_port,
                    prom_port,
                    grafana_port,
                    token,
                    "fixture-grafana-password-9031",
                ),
                run_seconds=5,
            )
            == 0
        )
        output = capsys.readouterr().out
        assert '"status": "READY"' in output
        assert token not in output and "fixture-grafana-password-9031" not in output
        assert list((tmp_path / "state").glob("observability-run-*")) == []
        assert _free_port_is_available(prom_port)
        assert _free_port_is_available(grafana_port)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
        telemetry.close()


def _listener(port: int) -> socket.socket:
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    return listener


def _free_port_is_available(port: int) -> bool:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True
