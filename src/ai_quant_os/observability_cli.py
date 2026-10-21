"""Opt-in local Prometheus/Grafana process supervisor for PAPER observations."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import tempfile
import time

import httpx

from ai_quant_os.local_processes import OwnedProcess


@dataclass(frozen=True)
class RuntimeSettings:
    checkout: Path
    state: Path
    api_port: int
    prometheus_port: int
    grafana_port: int
    operator_token: str
    grafana_password: str

    def validate(self) -> None:
        ports = (self.api_port, self.prometheus_port, self.grafana_port)
        if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
            raise ValueError("ports must be integers from 1 through 65535")
        if len(set(ports)) != len(ports):
            raise ValueError("API, Prometheus, and Grafana ports must be distinct")
        if not self.operator_token or "\n" in self.operator_token or "\r" in self.operator_token:
            raise ValueError("a single-line PAPER operator token is required")
        if len(self.grafana_password) < 12 or "\n" in self.grafana_password:
            raise ValueError("Grafana admin password must contain at least 12 characters")


@dataclass(frozen=True)
class PreparedRuntime:
    run_directory: Path
    prometheus_config: Path
    prometheus_arguments: list[str]
    prometheus_environment: dict[str, str]
    provisioning: Path
    dashboards: Path
    grafana_environment: dict[str, str]
    prometheus_binary: Path
    grafana_binary: Path
    grafana_home: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("pinned Grafana distribution contains a symlink")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    entries.sort(key=lambda entry: entry["path"])
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _verify_native(checkout: Path) -> tuple[Path, Path, Path]:
    if os.name != "nt":
        raise RuntimeError(
            "the pinned native observability launcher currently supports Windows only"
        )
    prom_manifest = json.loads(
        (checkout / "infra/observability/prometheus-release.json").read_text()
    )
    grafana_manifest = json.loads(
        (checkout / "infra/observability/grafana-release.json").read_text()
    )
    prom_home = checkout / "data-local/tools/prometheus-3.15.0"
    grafana_home = checkout / "data-local/tools/grafana-13.2.3"
    prom = prom_home / "prometheus.exe"
    grafana = grafana_home / "bin/grafana.exe"
    if not prom.is_file() or _sha256(prom) != prom_manifest["executables"][0]["sha256"]:
        raise RuntimeError("pinned Prometheus executable missing or hash mismatch")
    if (
        not grafana.is_file()
        or _sha256(grafana) != grafana_manifest["executable_sha256"]
        or _tree_sha256(grafana_home) != grafana_manifest["tree_sha256"]
    ):
        raise RuntimeError("pinned Grafana distribution missing or hash mismatch")
    return prom, grafana, grafana_home


def prepare_runtime(settings: RuntimeSettings, run_directory: Path) -> PreparedRuntime:
    settings.validate()
    checkout = settings.checkout.resolve()
    run_directory.mkdir(parents=True, exist_ok=False)
    for name in ("data", "logs", "plugins"):
        (settings.state / name).mkdir(parents=True, exist_ok=True)
    (settings.state / "prometheus").mkdir(parents=True, exist_ok=True)
    prom_binary, grafana_binary, grafana_home = _verify_native(checkout)

    source_config = json.loads((checkout / "infra/observability/prometheus.json").read_text())
    source_config["scrape_configs"][0]["static_configs"] = [
        {"targets": [f"127.0.0.1:{settings.api_port}"]}
    ]
    config = run_directory / "prometheus.json"
    config.write_text(json.dumps(source_config, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(
        checkout / "infra/observability/paper-http-alerts.json",
        run_directory / "paper-http-alerts.json",
    )
    (run_directory / "operator-token").write_text(settings.operator_token, encoding="utf-8")
    (run_directory / "operator-admin-password").write_text(
        settings.grafana_password, encoding="utf-8"
    )
    (run_directory / "operator-secret-key").write_text(secrets.token_urlsafe(48), encoding="utf-8")

    grafana_source = checkout / "infra/observability/grafana"
    provisioning = run_directory / "provisioning"
    shutil.copytree(grafana_source / "provisioning", provisioning)
    datasource = provisioning / "datasources/paper.yaml"
    datasource.write_text(
        datasource.read_text(encoding="utf-8").replace(
            "http://127.0.0.1:9090", f"http://127.0.0.1:{settings.prometheus_port}"
        ),
        encoding="utf-8",
    )
    dashboards = run_directory / "dashboards"
    shutil.copytree(grafana_source / "dashboards", dashboards)
    arguments = [
        str(prom_binary),
        f"--config.file={config}",
        f"--storage.tsdb.path={settings.state / 'prometheus'}",
        "--storage.tsdb.retention.time=3d",
        "--storage.tsdb.retention.size=128MB",
        f"--web.listen-address=127.0.0.1:{settings.prometheus_port}",
    ]
    secret_names = {"AI_QUANT_OPERATOR_TOKEN", "AI_QUANT_GRAFANA_ADMIN_PASSWORD"}
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GF_") and key not in secret_names
    }
    prom_env = dict(clean_env)
    grafana_env = {
        **clean_env,
        "GF_SERVER_HTTP_ADDR": "127.0.0.1",
        "GF_SERVER_HTTP_PORT": str(settings.grafana_port),
        "GF_PATHS_DATA": str(settings.state / "data"),
        "GF_PATHS_LOGS": str(settings.state / "logs"),
        "GF_PATHS_PLUGINS": str(settings.state / "plugins"),
        "GF_PATHS_PROVISIONING": str(provisioning),
        "AI_QUANT_GRAFANA_DASHBOARDS": str(dashboards),
    }
    return PreparedRuntime(
        run_directory,
        config,
        arguments,
        prom_env,
        provisioning,
        dashboards,
        grafana_env,
        prom_binary,
        grafana_binary,
        grafana_home,
    )


def _free_loopback_port(port: int) -> bool:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _wait_ready(owner: OwnedProcess, url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=0.5, trust_env=False, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            if owner.process.poll() is not None:
                raise RuntimeError("an observability process exited before becoming ready")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError("local observability service readiness timed out")


def run(settings: RuntimeSettings, run_seconds: int | None = None) -> int:
    settings.validate()
    if run_seconds is not None and not 1 <= run_seconds <= 60:
        raise ValueError("--run-seconds must be between 1 and 60")
    checkout = settings.checkout.resolve()
    for port in (settings.prometheus_port, settings.grafana_port):
        if not _free_loopback_port(port):
            raise RuntimeError(f"requested loopback port {port} is already in use")
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
        response = client.get(
            f"http://127.0.0.1:{settings.api_port}/api/v1/observability/metrics",
            headers={"X-Operator-Token": settings.operator_token},
        )
        if response.status_code != 200:
            raise RuntimeError(
                "PAPER API metrics preflight failed; start the local PAPER API first"
            )
    settings.state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="observability-run-", dir=settings.state) as temp:
        runtime = prepare_runtime(settings, Path(temp) / "private")
        with ExitStack() as stack:
            prom = OwnedProcess(
                runtime.prometheus_arguments,
                cwd=runtime.run_directory,
                environment=runtime.prometheus_environment,
            )
            stack.callback(prom.close)
            _wait_ready(prom, f"http://127.0.0.1:{settings.prometheus_port}/-/ready")
            wrapper = (
                "import subprocess,sys; p=subprocess.Popen(sys.argv[1:],stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL,shell=False,creationflags=0x08000000); raise SystemExit(p.wait())"
            )
            command = [
                sys.executable,
                "-c",
                wrapper,
                str(runtime.grafana_binary),
                "server",
                "--homepath=" + str(runtime.grafana_home),
                "--config=" + str(checkout / "infra/observability/grafana/grafana.ini"),
            ]
            grafana = OwnedProcess(
                command, cwd=runtime.run_directory, environment=runtime.grafana_environment
            )
            stack.callback(grafana.close)
            _wait_ready(grafana, f"http://127.0.0.1:{settings.grafana_port}/api/health")
            print(
                json.dumps(
                    {
                        "status": "READY",
                        "prometheus": f"http://127.0.0.1:{settings.prometheus_port}",
                        "grafana": f"http://127.0.0.1:{settings.grafana_port}",
                    }
                )
            )
            deadline = time.monotonic() + run_seconds if run_seconds else None
            while True:
                if prom.process.poll() is not None or grafana.process.poll() is not None:
                    raise RuntimeError("an observability process exited unexpectedly")
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run local PAPER Prometheus and Grafana")
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, default=Path("data-local/observability"))
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--prometheus-port", type=int, default=9090)
    parser.add_argument("--grafana-port", type=int, default=3100)
    parser.add_argument("--run-seconds", type=int)
    args = parser.parse_args()
    token, password = (
        os.getenv("AI_QUANT_OPERATOR_TOKEN", ""),
        os.getenv("AI_QUANT_GRAFANA_ADMIN_PASSWORD", ""),
    )
    if not token or not password:
        parser.error(
            "set AI_QUANT_OPERATOR_TOKEN and AI_QUANT_GRAFANA_ADMIN_PASSWORD in this terminal"
        )
    try:
        return run(
            RuntimeSettings(
                args.checkout.resolve(),
                args.state.resolve(),
                args.api_port,
                args.prometheus_port,
                args.grafana_port,
                token,
                password,
            ),
            args.run_seconds,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as error:
        print(f"observability launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
