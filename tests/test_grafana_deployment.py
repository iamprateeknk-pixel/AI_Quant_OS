"""Pinned native Grafana provisions and queries actual paper/Prometheus observations."""

from contextlib import ExitStack
from pathlib import Path
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_quant_os.api import create_app
from ai_quant_os.local_processes import OwnedProcess
from ai_quant_os.paper_telemetry import PaperTelemetry
from tests.test_paper_telemetry import NOW, payload
from tests.test_prometheus_deployment import native_tools  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "infra/observability/grafana"
UID = "paper-http-source"
DASHBOARD = "paper-http-observations"


def _verify_tree(home):
    entries = []
    for path in sorted(home.rglob("*")):
        assert not path.is_symlink()
        if path.is_file():
            raw = path.read_bytes()
            entries.append(
                {
                    "path": path.relative_to(home).as_posix(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    entries.sort(key=lambda e: e["path"])
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture(scope="module")
def native_grafana():
    value = os.getenv("AI_QUANT_NATIVE_GRAFANA_HOME")
    if not value:
        pytest.skip("Explicit pinned native Grafana distribution required")
    home = Path(value).resolve()
    manifest = json.loads((ROOT / "infra/observability/grafana-release.json").read_text())
    assert manifest["version"] == "13.2.3" and manifest["platform"] == "windows_amd64"
    assert _verify_tree(home) == manifest["tree_sha256"]
    result = subprocess.run(
        [str(home / "bin/grafana.exe"), "--version"], capture_output=True, timeout=30
    )
    assert result.returncode == 0 and b"13.2.3" in result.stdout + result.stderr
    yield home
    assert _verify_tree(home) == manifest["tree_sha256"]


def _capture_job_handles(owner):
    """Observe exact handles belonging to the existing suspended-before-assignment job."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)

    class Inventory(ctypes.Structure):
        _fields_ = [
            ("assigned", wintypes.DWORD),
            ("listed", wintypes.DWORD),
            ("pids", ctypes.c_size_t * 128),
        ]

    functions = [
        (
            "QueryInformationJobObject",
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (
            "IsProcessInJob",
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        ),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ]
    for name, arguments, result in functions:
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    inventory = Inventory()
    assert api.QueryInformationJobObject(
        owner._job.handle, 3, ctypes.byref(inventory), ctypes.sizeof(inventory), None
    )
    assert inventory.assigned == inventory.listed <= 128
    handles = []
    try:
        for pid in inventory.pids[: inventory.listed]:
            handle = api.OpenProcess(0x101000, False, pid)
            assert handle
            handles.append(handle)
            member = wintypes.BOOL()
            assert (
                api.IsProcessInJob(handle, owner._job.handle, ctypes.byref(member)) and member.value
            )
        return api, handles
    except BaseException:
        for handle in handles:
            api.CloseHandle(handle)
        raise


def _close_original(owner, path):
    try:
        api, handles = _capture_job_handles(owner)
    except BaseException:
        owner.close()
        assert owner.process.poll() is not None
        raise
    try:
        owner.close()
        assert owner.process.poll() is not None
        assert all(api.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
        path.write_text(
            json.dumps(
                {"captured_owned_process_handles": len(handles), "all_observed_terminal": True}
            )
            + "\n"
        )
    finally:
        for handle in handles:
            assert api.CloseHandle(handle)


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _environment(tmp_path, port):
    for name in ("data", "logs", "plugins"):
        (tmp_path / name).mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GF_")}
    return {
        **env,
        "GF_SERVER_HTTP_PORT": str(port),
        "GF_PATHS_DATA": str(tmp_path / "data"),
        "GF_PATHS_LOGS": str(tmp_path / "logs"),
        "GF_PATHS_PLUGINS": str(tmp_path / "plugins"),
        "GF_PATHS_PROVISIONING": str(tmp_path / "provisioning"),
        "AI_QUANT_GRAFANA_DASHBOARDS": str(tmp_path / "dashboards"),
    }


def _grafana_command(home):
    # The trusted wrapper inherits the already assigned job before spawning native workers.
    capture = (
        "import subprocess,sys; "
        "log=open('native-startup.log','xb'); "
        "process=subprocess.Popen(sys.argv[1:],stdout=log,stderr=log,shell=False,creationflags=0x08000000); "
        "code=process.wait(); log.close(); raise SystemExit(code)"
    )
    return [
        sys.executable,
        "-c",
        capture,
        str(home / "bin/grafana.exe"),
        "server",
        "--homepath=" + str(home),
        "--config=" + str(ASSETS / "grafana.ini"),
    ]


@pytest.mark.parametrize("missing", ["operator-admin-password", "operator-secret-key"])
def test_native_grafana_refuses_missing_private_bootstrap_files(native_grafana, tmp_path, missing):
    if missing == "operator-secret-key":
        (tmp_path / "operator-admin-password").write_text(secrets.token_urlsafe(32) + "Aa0!")
    owner = OwnedProcess(
        _grafana_command(native_grafana),
        cwd=tmp_path,
        environment=_environment(tmp_path, _port()),
    )
    try:
        assert owner.process.wait(timeout=20) != 0
        assert missing in (tmp_path / "native-startup.log").read_text(errors="replace")
    finally:
        _close_original(owner, tmp_path / "missing-bootstrap-terminal.json")


def test_native_grafana_auth_provisioning_query_and_source_failure(
    native_grafana, request, tmp_path
):
    prometheus_home = request.getfixturevalue("native_tools")
    assert os.name == "nt", "This native custody profile is Windows"
    with ExitStack() as stack:
        telemetry = PaperTelemetry(exporter=InMemorySpanExporter())
        stack.callback(telemetry.close)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        stack.callback(listener.close)
        paper_port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(
                    operator_token="fixture-grafana-paper", clock=lambda: NOW, telemetry=telemetry
                ),
                log_level="error",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()

        def stop_paper():
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()

        stack.callback(stop_paper)
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and thread.is_alive()
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as paper:
            base = f"http://127.0.0.1:{paper_port}"
            headers = {
                "X-Operator-Token": "fixture-grafana-paper",
                "Idempotency-Key": "fixture-grafana-order",
            }
            assert (
                paper.post(
                    base + "/api/v1/paper/quotes",
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
            accepted = paper.post(base + "/api/v1/paper/intents", headers=headers, json=payload())
            assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
            replay = paper.post(base + "/api/v1/paper/intents", headers=headers, json=payload())
            assert replay.json() == accepted.json()
        prom_port, grafana_port = _port(), _port()
        prom_config = json.loads((ROOT / "infra/observability/prometheus.json").read_text())
        prom_config["global"] = {"scrape_interval": "1s", "scrape_timeout": "500ms"}
        prom_config["scrape_configs"][0]["static_configs"] = [
            {"targets": [f"127.0.0.1:{paper_port}"]}
        ]
        (tmp_path / "prometheus.json").write_text(json.dumps(prom_config))
        token_file = tmp_path / "operator-token"
        token_file.write_text("fixture-grafana-paper")
        prom = OwnedProcess(
            [
                str(prometheus_home / "prometheus.exe"),
                "--config.file=" + str(tmp_path / "prometheus.json"),
                "--storage.tsdb.path=" + str(tmp_path / "tsdb"),
                "--storage.tsdb.retention.time=3d",
                "--storage.tsdb.retention.size=128MB",
                f"--web.listen-address=127.0.0.1:{prom_port}",
                "--log.level=warn",
            ],
            cwd=tmp_path,
            environment=os.environ.copy(),
        )
        stack.callback(_close_original, prom, tmp_path / "prometheus-terminal.json")
        shutil.copytree(ASSETS / "provisioning", tmp_path / "provisioning")
        shutil.copytree(ASSETS / "dashboards", tmp_path / "dashboards")
        datasource_file = tmp_path / "provisioning/datasources/paper.yaml"
        datasource_file.write_text(
            datasource_file.read_text().replace("127.0.0.1:9090", f"127.0.0.1:{prom_port}")
        )
        password = secrets.token_urlsafe(32) + "Aa0!"
        secret_key = secrets.token_urlsafe(48)
        (tmp_path / "operator-admin-password").write_text(password)
        (tmp_path / "operator-secret-key").write_text(secret_key)
        grafana = OwnedProcess(
            _grafana_command(native_grafana),
            cwd=tmp_path,
            environment=_environment(tmp_path, grafana_port),
        )
        stack.callback(_close_original, grafana, tmp_path / "grafana-terminal.json")
        url = f"http://127.0.0.1:{grafana_port}"
        with (
            httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as anonymous,
            httpx.Client(
                timeout=3,
                trust_env=False,
                follow_redirects=False,
                auth=("paper-operator", password),
            ) as client,
        ):
            deadline = time.monotonic() + 45
            while True:
                assert grafana.process.poll() is None, "Original Grafana exited"
                try:
                    health = anonymous.get(url + "/api/health")
                    if health.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert health.json()["version"] == "13.2.3"
            assert anonymous.get(url + f"/api/dashboards/uid/{DASHBOARD}").status_code == 401
            assert anonymous.get(url + "/api/datasources").status_code == 401
            dashboard = client.get(url + f"/api/dashboards/uid/{DASHBOARD}")
            assert dashboard.status_code == 200 and dashboard.json()["meta"]["provisioned"] is True
            assert dashboard.json()["dashboard"]["editable"] is False
            original_dashboard = json.loads((ASSETS / "dashboards/paper-http.json").read_text())
            original_expressions = [
                target["expr"]
                for panel in original_dashboard["panels"]
                for target in panel.get("targets", [])
            ]
            saved_expressions = [
                target["expr"]
                for panel in dashboard.json()["dashboard"]["panels"]
                for target in panel.get("targets", [])
            ]
            assert saved_expressions == original_expressions
            denied_save = client.post(
                url + "/api/dashboards/db",
                json={"dashboard": dashboard.json()["dashboard"], "overwrite": True},
            )
            assert denied_save.status_code == 400
            assert "provisioned" in denied_save.text.lower()
            source = client.get(url + f"/api/datasources/uid/{UID}").json()
            assert source["url"] == f"http://127.0.0.1:{prom_port}" and source["readOnly"] is True
            settings = client.get(url + "/api/admin/settings").json()
            assert settings["auth.anonymous"]["enabled"] == "false"
            assert settings["analytics"]["reporting_enabled"] == "false"
            assert settings["plugins"]["preinstall_disabled"] == "true"
            serialized = json.dumps([dashboard.json(), source, settings])
            if (
                password in serialized
                or secret_key in serialized
                or "fixture-grafana-paper" in serialized
            ):
                raise AssertionError(
                    "Native output exposed private fixture credentials; values withheld"
                )
            selector = (
                'method="POST",route="/api/v1/paper/intents",status_class="2xx",job="paper_http"'
            )

            def proxy_query(expr, expected):
                deadline = time.monotonic() + 30
                while True:
                    assert grafana.process.poll() is None and prom.process.poll() is None
                    response = client.get(
                        url + f"/api/datasources/proxy/uid/{UID}/api/v1/query",
                        params={"query": expr},
                    )
                    if response.status_code == 200:
                        rows = response.json()["data"]["result"]
                        if len(rows) == 1 and float(rows[0]["value"][1]) == expected:
                            return
                    assert time.monotonic() < deadline, "Original Grafana source query deadline"
                    time.sleep(0.1)

            proxy_query('up{job="paper_http"}', 1)
            proxy_query("ai_quant_paper_http_duration_seconds_count{" + selector + "}", 2)
            query = {
                "queries": [
                    {
                        "refId": "A",
                        "datasource": {"type": "prometheus", "uid": UID},
                        "expr": "ai_quant_paper_http_duration_seconds_count{" + selector + "}",
                        "instant": True,
                        "range": False,
                        "format": "time_series",
                        "intervalMs": 1000,
                        "maxDataPoints": 100,
                    }
                ],
                "from": str(int(time.time() * 1000) - 60000),
                "to": str(int(time.time() * 1000)),
            }
            result = client.post(url + "/api/ds/query", json=query)
            assert result.status_code == 200 and result.json()["results"]["A"]["frames"][0]["data"][
                "values"
            ][1] == [2]
            token_file.write_text("invalid-grafana-fixture")
            proxy_query('up{job="paper_http"}', 0)
            proxy_query(
                "max_over_time(ai_quant_paper_http_duration_seconds_count{" + selector + "}[5m])", 2
            )
            query["queries"][0]["expr"] = original_expressions[2]
            query["to"] = str(int(time.time() * 1000))
            query["from"] = str(int(query["to"]) - 60000)
            gated = client.post(url + "/api/ds/query", json=query)
            assert gated.status_code == 200
            (tmp_path / "gated-current-observation.json").write_text(
                json.dumps({"query": query, "response": gated.json()}, indent=2)
            )
            gated_result = gated.json()["results"]["A"]
            assert gated_result["status"] == 200 and "error" not in gated_result
            for frame in gated_result.get("frames", []):
                fields = frame["schema"]["fields"]
                columns = frame["data"]["values"]
                assert len(fields) == len(columns)
                for field, values in zip(fields, columns, strict=True):
                    if field["type"] == "number":
                        assert all(value is None for value in values)
                assert not fields or any(field["type"] == "number" for field in fields)
            with httpx.Client(timeout=2, trust_env=False) as paper:
                assert len(paper.get(base + "/api/v1/orders").json()["orders"]) == 1
                halt = paper.post(
                    base + "/api/v1/control/halt",
                    headers={
                        "X-Operator-Token": "fixture-grafana-paper",
                        "Idempotency-Key": "fixture-grafana-halt",
                    },
                    json={"reason": "fixture monitoring failed"},
                )
                assert halt.status_code == 200 and halt.json()["safety_state"] == "HALT"
            (tmp_path / "grafana-observation.json").write_text(
                json.dumps(
                    {
                        "version": "13.2.3",
                        "provisioned_uid": DASHBOARD,
                        "original_order_requests": 2,
                        "native_query_frame": result.json(),
                        "gated_failure_query_frame": gated.json(),
                        "rejected_scrape_observed": True,
                        "halt": "HALT",
                        "browser_verified": False,
                    },
                    indent=2,
                )
            )
