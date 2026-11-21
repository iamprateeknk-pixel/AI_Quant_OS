import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import socket
import ssl
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
    prometheus = tmp_path / "native" / "prometheus.exe"
    grafana_home = tmp_path / "native" / "grafana"
    grafana = grafana_home / "bin" / "grafana.exe"
    monkeypatch.setattr(
        "ai_quant_os.observability_cli._verify_native",
        lambda _checkout: (prometheus, grafana, grafana_home),
    )
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
    assert "alerting" not in config
    assert runtime.alertmanager_config is None
    assert runtime.alertmanager_arguments is None
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


def test_prepare_runtime_adds_secret_backed_external_webhook_only_when_configured(
    tmp_path: Path, monkeypatch
):
    assert "webhook_url_file" in RuntimeSettings.__dataclass_fields__
    prometheus = tmp_path / "native" / "prometheus.exe"
    grafana_home = tmp_path / "native" / "grafana"
    grafana = grafana_home / "bin" / "grafana.exe"
    alertmanager = tmp_path / "native" / "alertmanager.exe"
    monkeypatch.setattr(
        "ai_quant_os.observability_cli._verify_native",
        lambda _checkout: (prometheus, grafana, grafana_home),
    )
    monkeypatch.setattr(
        "ai_quant_os.observability_cli._verify_alertmanager",
        lambda _checkout: alertmanager,
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    webhook_url = "https://hooks.example.com/services/private-webhook-key"
    webhook_file = tmp_path / "webhook-url"
    webhook_file.write_text(webhook_url, encoding="utf-8")
    settings = RuntimeSettings(
        checkout=Path(__file__).resolve().parents[1],
        state=tmp_path / "persistent",
        api_port=8124,
        prometheus_port=9190,
        grafana_port=3190,
        operator_token="synthetic-paper-token",
        grafana_password="synthetic-grafana-password-A9!",
        alertmanager_port=9193,
        webhook_url_file=webhook_file,
    )

    runtime = prepare_runtime(settings, tmp_path / "run")

    prometheus_config = json.loads(runtime.prometheus_config.read_text(encoding="utf-8"))
    assert prometheus_config["alerting"]["alertmanagers"][0]["static_configs"] == [
        {"targets": ["127.0.0.1:9193"]}
    ]
    notification_config = json.loads(runtime.alertmanager_config.read_text(encoding="utf-8"))
    assert notification_config["receivers"] == [
        {
            "name": "paper-webhook",
            "webhook_configs": [
                {
                    "url_file": "webhook-url",
                    "send_resolved": True,
                    "max_alerts": 2,
                    "timeout": "5s",
                    "http_config": {
                        "follow_redirects": False,
                        "proxy_from_environment": False,
                    },
                }
            ],
        }
    ]
    assert (runtime.run_directory / "webhook-url").read_text(encoding="utf-8") == webhook_url
    assert runtime.alertmanager_arguments[-1] == "--web.listen-address=127.0.0.1:9193"
    assert webhook_url not in runtime.alertmanager_config.read_text(encoding="utf-8")
    assert webhook_url not in runtime.alertmanager_environment.values()
    assert not any(
        key.lower() in {"http_proxy", "https_proxy", "all_proxy"}
        for key in runtime.alertmanager_environment
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/private-key",
        "https://user:password@hooks.example.com/private-key",
        "https://hooks.example.com/private-key#fragment",
    ],
)
def test_runtime_rejects_unsafe_notification_url(tmp_path: Path, url: str):
    webhook_file = tmp_path / "webhook-url"
    webhook_file.write_text(url, encoding="utf-8")
    settings = RuntimeSettings(
        checkout=Path(__file__).resolve().parents[1],
        state=tmp_path / "state",
        api_port=8000,
        prometheus_port=9090,
        grafana_port=3100,
        operator_token="token",
        grafana_password="password",
        webhook_url_file=webhook_file,
    )
    with pytest.raises(ValueError, match="HTTPS webhook URL"):
        settings.validate()


def test_pinned_alertmanager_accepts_generated_secret_backed_config_and_runs_loopback(
    tmp_path: Path, monkeypatch
):
    if os.name != "nt":
        pytest.skip("Pinned native Alertmanager is installed for Windows acceptance")
    root = Path(__file__).resolve().parents[1]
    binary = root / "data-local/tools/alertmanager-0.35.0/alertmanager.exe"
    if not binary.is_file():
        pytest.skip("Pinned Alertmanager distribution is not installed")
    prometheus = tmp_path / "native" / "prometheus.exe"
    grafana_home = tmp_path / "native" / "grafana"
    grafana = grafana_home / "bin" / "grafana.exe"
    monkeypatch.setattr(
        "ai_quant_os.observability_cli._verify_native",
        lambda _checkout: (prometheus, grafana, grafana_home),
    )
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loopback-alert-test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "receiver-cert.pem"
    key_path = tmp_path / "receiver-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    delivered = threading.Event()
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            delivered.set()

        def log_message(self, _format, *args):
            return

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    receiver.socket = tls.wrap_socket(receiver.socket, server_side=True)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    webhook_file = tmp_path / "private-webhook-url"
    webhook_file.write_text(
        f"https://127.0.0.1:{receiver.server_address[1]}/alerts", encoding="utf-8"
    )
    port = _free_port()
    settings = RuntimeSettings(
        checkout=root,
        state=tmp_path / "state",
        api_port=_free_port(),
        prometheus_port=_free_port(),
        grafana_port=_free_port(),
        operator_token="fixture-operator-token",
        grafana_password="fixture-grafana-password-9031",
        alertmanager_port=port,
        webhook_url_file=webhook_file,
    )
    runtime = prepare_runtime(settings, tmp_path / "run")
    notification_config = json.loads(runtime.alertmanager_config.read_text(encoding="utf-8"))
    notification_config["route"]["group_wait"] = "1s"
    notification_config["receivers"][0]["webhook_configs"][0]["http_config"] = {
        "tls_config": {"ca_file": str(cert_path)}
    }
    runtime.alertmanager_config.write_text(json.dumps(notification_config), encoding="utf-8")

    from ai_quant_os.local_processes import OwnedProcess
    from ai_quant_os.observability_cli import _wait_ready

    assert runtime.alertmanager_arguments is not None
    owner = None
    try:
        receiver_thread.start()
        owner = OwnedProcess(
            runtime.alertmanager_arguments,
            cwd=runtime.run_directory,
            environment=runtime.alertmanager_environment or {},
        )
        _wait_ready(owner, f"http://127.0.0.1:{port}/-/ready", timeout=15)
        import httpx

        start = datetime.now(timezone.utc)
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/api/v2/alerts",
                json=[
                    {
                        "labels": {
                            "alertname": "SyntheticWebhookAcceptance",
                            "severity": "warning",
                        },
                        "annotations": {"summary": "Loopback-only delivery test"},
                        "startsAt": start.isoformat(),
                        "endsAt": (start + timedelta(minutes=5)).isoformat(),
                        "generatorURL": "http://127.0.0.1:9090/graph",
                    }
                ],
            )
        assert response.status_code == 200
        assert delivered.wait(timeout=10)
        assert received[0]["status"] == "firing"
        assert received[0]["alerts"][0]["labels"]["alertname"] == "SyntheticWebhookAcceptance"
    finally:
        if owner is not None:
            owner.close()
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=5)
        assert not receiver_thread.is_alive()


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
