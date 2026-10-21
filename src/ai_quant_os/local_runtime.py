"""Local persistent paper API and production-built monitoring UI supervisor."""

import json
import math
import os
import secrets
import shutil
import socket
import sys
import subprocess
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ai_quant_os.local_processes import OwnedProcess
from ai_quant_os.repair_transport import bounded_command


def _isolated_module_command(module: str, source: Path, *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-c",
        "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
        "module = sys.argv.pop(1); runpy.run_module(module, run_name='__main__')",
        str(source),
        module,
        *arguments,
    ]


@dataclass(frozen=True)
class LocalSettings:
    checkout: Path
    state: Path
    api_port: int = 8000
    web_port: int = 3000

    def validate(self) -> None:
        if (
            any(
                type(port) is not int or not 1 <= port <= 65535
                for port in (self.api_port, self.web_port)
            )
            or self.api_port == self.web_port
        ):
            raise ValueError("Distinct valid local ports required")
        for port in (self.api_port, self.web_port):
            try:
                with socket.socket() as listener:
                    if os.name == "nt":
                        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    else:
                        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError("Requested local port is already occupied") from None
        web = self.checkout.resolve() / "apps/web"
        if (
            not (web / "node_modules/next/dist/bin/next").is_file()
            or not (web / ".next/BUILD_ID").is_file()
            or shutil.which("node") is None
        ):
            raise ValueError("Install frontend dependencies and build Mission Control first")
        if any(
            (web / name).exists()
            for name in (".env", ".env.local", ".env.production", ".env.production.local")
        ):
            raise ValueError("Local launcher requires frontend environment files to be absent")

    def environments(self, inherited: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        base = {
            key: value
            for key, value in inherited.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            }
        }
        backend = {
            **base,
            "AI_QUANT_PAPER_DB": str(self.state.resolve() / "paper.sqlite3"),
            "AI_QUANT_DEMO_SYNC_STATE": str(self.state.resolve() / "demo-sync"),
            "AI_QUANT_RESEARCH_WORKSPACE": str(self.state.resolve() / "research"),
            "AI_QUANT_JEV_ENABLED": "false",
            "AI_QUANT_REVIEW_ENABLED": "false",
            "AI_QUANT_DAILY_EVIDENCE_ENABLED": "0",
            "AI_QUANT_TEMPORAL_RECOVERY_ENABLED": "0",
            "AI_QUANT_HEALTH_WATCHDOG_ENABLED": "0",
        }
        if inherited.get("AI_QUANT_OPERATOR_TOKEN"):
            backend["AI_QUANT_OPERATOR_TOKEN"] = inherited["AI_QUANT_OPERATOR_TOKEN"]
        alerts_enabled = inherited.get("AI_QUANT_PROMETHEUS_ALERTS_ENABLED", "0")
        if alerts_enabled not in {"0", "1"}:
            raise ValueError("Explicit local monitoring alert enable flag required")
        backend["AI_QUANT_PROMETHEUS_ALERTS_ENABLED"] = alerts_enabled
        telemetry_enabled = inherited.get("AI_QUANT_OTEL_ENABLED", "0")
        if telemetry_enabled not in {"0", "1"}:
            raise ValueError("Explicit OTel enable flag required")
        backend["AI_QUANT_OTEL_ENABLED"] = telemetry_enabled
        if telemetry_enabled == "1":
            from ai_quant_os.paper_telemetry import LocalOtlpExporter

            endpoint = inherited.get("AI_QUANT_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
            LocalOtlpExporter(endpoint)
            backend["AI_QUANT_OTEL_ENDPOINT"] = endpoint
        collector_managed = inherited.get("AI_QUANT_OTEL_COLLECTOR_MANAGED", "0")
        if collector_managed not in {"0", "1"}:
            raise ValueError("Explicit local Collector management flag required")
        if collector_managed == "1":
            if telemetry_enabled != "1":
                raise ValueError("Managed Collector requires OTel telemetry to be enabled")
            backend["AI_QUANT_COLLECTOR_TRACE_FILE"] = str(
                self.state.resolve() / "observability/paper-traces.jsonl"
            )
        frontend = {
            **base,
            "AI_QUANT_API_URL": f"http://127.0.0.1:{self.api_port}",
            "AI_QUANT_WEB_ORIGIN": f"http://127.0.0.1:{self.web_port}",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NODE_ENV": "production",
        }
        return backend, frontend


def wait_ready(
    process: OwnedProcess, url: str, accepts: Callable[[bytes], bool], *, timeout: float
) -> None:
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("Invalid local readiness deadline")
    deadline = time.monotonic() + timeout
    probe_environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
    }
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError("Owned local service exited before readiness")
        try:
            response = bounded_command(
                _isolated_module_command(
                    "ai_quant_os.local_probe", Path(__file__).resolve().parents[1], url
                ),
                environment=probe_environment,
                timeout=min(2, max(0.01, deadline - time.monotonic())),
                output_limit=262144,
            )
            if response.returncode == 0 and accepts(response.stdout):
                if process.process.poll() is None:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Local service readiness deadline exceeded")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _start_managed_collector(settings: LocalSettings, environment: dict[str, str]):
    checkout = settings.checkout.resolve()
    manifest = json.loads((checkout / "infra/observability/collector-release.json").read_text())
    binary = checkout / "data-local/tools/otelcol-contrib-0.162.0/otelcol-contrib.exe"
    if not binary.is_file() or _sha256(binary) != manifest["executable_sha256"]:
        raise ValueError("Pinned OpenTelemetry Collector missing or hash mismatch")
    endpoint = urlsplit(environment["AI_QUANT_OTEL_ENDPOINT"])
    port = endpoint.port
    if port is None or port in {settings.api_port, settings.web_port}:
        raise ValueError("Managed Collector requires an unused distinct loopback port")
    try:
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as error:
        raise ValueError("Managed Collector loopback port is already occupied") from error

    trace_file = Path(environment["AI_QUANT_COLLECTOR_TRACE_FILE"])
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads((checkout / "infra/observability/collector.json").read_text())
    config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{port}"
    config_path = trace_file.parent / "collector-runtime.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    collector_environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
        if os.environ.get(key)
    }
    collector_environment["AI_QUANT_COLLECTOR_TRACE_FILE"] = str(trace_file)
    result = subprocess.run(
        [str(binary), "validate", f"--config={config_path}"],
        cwd=checkout,
        env=collector_environment,
        capture_output=True,
        timeout=15,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise ValueError("Pinned OpenTelemetry Collector refused its local configuration")
    return binary, config_path, collector_environment, port


def _wait_collector(process: OwnedProcess, port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError("Owned OpenTelemetry Collector exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Owned OpenTelemetry Collector readiness deadline exceeded")


@contextmanager
def local_application(settings: LocalSettings):
    settings.validate()
    settings.state.resolve().mkdir(parents=True, exist_ok=True)
    inherited_environment = dict(os.environ)
    backend_env, frontend_env = settings.environments(inherited_environment)
    nonce = secrets.token_hex(16)
    backend_env["AI_QUANT_LOCAL_INSTANCE"] = nonce
    collector_managed = inherited_environment.get("AI_QUANT_OTEL_COLLECTOR_MANAGED", "0") == "1"
    with ExitStack() as stack:
        children = []
        if collector_managed:
            binary, config_path, collector_environment, collector_port = _start_managed_collector(
                settings, backend_env
            )
            collector = OwnedProcess(
                [str(binary), f"--config={config_path}"],
                cwd=settings.checkout.resolve(),
                environment=collector_environment,
            )
            stack.callback(collector.close)
            _wait_collector(collector, collector_port)
            children.append(collector)
        api = OwnedProcess(
            _isolated_module_command(
                "ai_quant_os.local_api", Path(__file__).resolve().parents[1], str(settings.api_port)
            ),
            cwd=settings.checkout.resolve(),
            environment=backend_env,
        )
        stack.callback(api.close)
        wait_ready(
            api,
            f"http://127.0.0.1:{settings.api_port}/_local/ready",
            lambda body: json.loads(body) == {"instance": nonce, "mode": "PAPER"},
            timeout=30,
        )
        web = OwnedProcess(
            [
                shutil.which("node"),
                str(settings.checkout.resolve() / "apps/web/node_modules/next/dist/bin/next"),
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(settings.web_port),
            ],
            cwd=settings.checkout.resolve() / "apps/web",
            environment=frontend_env,
        )
        stack.callback(web.close)
        wait_ready(
            web,
            f"http://127.0.0.1:{settings.web_port}",
            lambda body: b"Mission Control" in body and b"PAPER" in body,
            timeout=30,
        )
        children.extend((api, web))
        yield tuple(children)
