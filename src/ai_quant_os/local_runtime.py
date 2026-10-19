"""Local persistent paper API and production-built monitoring UI supervisor."""

import json
import math
import os
import secrets
import shutil
import socket
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


@contextmanager
def local_application(settings: LocalSettings):
    settings.validate()
    settings.state.resolve().mkdir(parents=True, exist_ok=True)
    backend_env, frontend_env = settings.environments(dict(os.environ))
    nonce = secrets.token_hex(16)
    backend_env["AI_QUANT_LOCAL_INSTANCE"] = nonce
    with ExitStack() as stack:
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
        yield api, web
