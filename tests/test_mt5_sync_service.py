import importlib.util
import json
from datetime import timedelta
from threading import Event

import pytest

from test_mt5_account_sync import NOW, sync_request
from test_mt5_sync_controller import setup as controller_setup


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_sync_service") is not None
    from ai_quant_os import mt5_sync_service

    return mt5_sync_service


def config_document(enabled=True):
    connection = sync_request(enabled).connection.model_dump(mode="json")
    connection.pop("password")
    source = sync_request(enabled).connection
    connection.update(
        terminal_path=source.terminal_path,
        expected_login=source.expected_login,
        expected_server=source.expected_server.get_secret_value(),
    )
    return {
        "connection": connection,
        "schedule": {
            "enabled": enabled,
            "initial_history_start": (NOW - timedelta(hours=1)).isoformat(),
        },
        "max_ticks": 3,
        "timer_seconds": 1,
    }


def test_config_password_is_loaded_only_from_fixed_environment(tmp_path):
    module = implementation()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_document()), encoding="utf-8")
    config = module.load_config(path, {"AI_QUANT_MT5_PASSWORD": "fixture-private-password"})
    assert config.connection.password.get_secret_value() == "fixture-private-password"
    assert "fixture-private-password" not in repr(config)


@pytest.mark.parametrize(
    "raw", ['{"connection":{},"connection":{}}', "[]", '{"password":"fixture-private"}']
)
def test_invalid_config_has_sanitized_error(tmp_path, raw):
    module = implementation()
    path = tmp_path / "config.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(module.ServiceRejected, match="^CONFIG_INVALID$"):
        module.load_config(path, {})


def test_enabled_config_requires_password(tmp_path):
    module = implementation()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_document()), encoding="utf-8")
    with pytest.raises(module.ServiceRejected, match="^CONFIG_INVALID$"):
        module.load_config(path, {})


def test_service_timer_advances_controller_and_publishes_stopped_health(tmp_path):
    module = implementation()
    _, controller, _, _, _, time, calls, _ = controller_setup(tmp_path)
    stop = Event()

    def wait(seconds):
        time[0] += timedelta(seconds=seconds)
        return False

    result = module.run_service(
        controller,
        tmp_path / "health.json",
        stop,
        max_ticks=3,
        timer_seconds=1,
        clock=lambda: time[0],
        wait=wait,
    )
    assert result.state == "STOPPED" and result.code == "TICK_LIMIT" and result.ticks == 3
    assert result.health.state == "HEALTHY" and len(calls) == 1
    view = module.read_health(tmp_path / "health.json", clock=lambda: time[0])
    assert view.state == "AVAILABLE" and view.record == result
    assert "fixture-demo-read" not in (tmp_path / "health.json").read_text()


def test_stopped_event_prevents_first_capture(tmp_path):
    module = implementation()
    _, controller, _, _, _, time, calls, _ = controller_setup(tmp_path)
    stop = Event()
    stop.set()
    result = module.run_service(controller, tmp_path / "health.json", stop, clock=lambda: time[0])
    assert result.state == "STOPPED" and result.ticks == 0 and calls == []


def test_halted_controller_stops_loop_without_wait(tmp_path):
    module = implementation()
    _, controller, _, _, _, time, calls, _ = controller_setup(tmp_path)
    time[0] += timedelta(days=2)

    def forbidden(seconds):
        raise AssertionError("No wait after halt")

    result = module.run_service(
        controller, tmp_path / "health.json", Event(), clock=lambda: time[0], wait=forbidden
    )
    assert result.state == "HALTED" and result.health.code == "HISTORY_GAP" and calls == []


def test_publication_failure_stops_before_capture(tmp_path, monkeypatch):
    module = implementation()
    _, controller, _, _, _, time, calls, _ = controller_setup(tmp_path)

    def fail(*args):
        raise OSError("fixture private location")

    monkeypatch.setattr(module, "publish_health", fail)
    with pytest.raises(module.ServiceRejected, match="^HEALTH_UNAVAILABLE$"):
        module.run_service(controller, tmp_path / "health.json", Event(), clock=lambda: time[0])
    assert calls == []


def test_health_read_labels_old_records_and_rejects_future_publication(tmp_path):
    module = implementation()
    _, controller, _, _, _, time, _, _ = controller_setup(tmp_path)
    path = tmp_path / "health.json"
    module.run_service(controller, path, Event(), max_ticks=1, clock=lambda: time[0])
    assert module.read_health(path, clock=lambda: NOW + timedelta(seconds=31)).state == "STALE"
    assert module.read_health(path, clock=lambda: NOW - timedelta(seconds=1)).state == "UNAVAILABLE"
    path.write_text("private malformed payload")
    assert module.read_health(path).state == "UNAVAILABLE"


def test_enabled_cli_requires_explicit_launch_flag_before_storage(tmp_path, monkeypatch, capsys):
    module = implementation()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_document()), encoding="utf-8")
    monkeypatch.setenv(module.PASSWORD_ENV, "fixture-private-password")
    directory = tmp_path / "state"
    assert module.main(["--config", str(path), "--state-dir", str(directory)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "ENABLE_REQUIRED" and not directory.exists()


def test_real_disabled_cli_creates_health_without_sdk_import(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_document(False)), encoding="utf-8")
    directory = tmp_path / "state"
    environment = dict(os.environ)
    environment.pop("AI_QUANT_MT5_PASSWORD", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.mt5_sync_service",
            "--config",
            str(path),
            "--state-dir",
            str(directory),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    public = json.loads(result.stdout)
    assert public["state"] == "DISABLED" and public["health"]["revision"] == 0
    assert public["may_submit_orders"] is False
    assert "fixture-demo-server" not in result.stdout and "610001" not in result.stdout
    assert implementation().read_health(directory / "health.json").state == "AVAILABLE"


def test_health_publication_failure_keeps_previous_complete_file(tmp_path, monkeypatch):
    module = implementation()
    from uuid import uuid4

    path = tmp_path / "health.json"
    first = module.ServiceHealth(
        run_id=uuid4(),
        state="RUNNING",
        ticks=0,
        published_at=NOW,
        valid_until=NOW + timedelta(seconds=30),
    )
    module.publish_health(path, first)
    before = path.read_bytes()

    def unavailable(*args):
        raise OSError("fixture replace failure")

    monkeypatch.setattr(module.os, "replace", unavailable)
    with pytest.raises(OSError):
        module.publish_health(path, first.model_copy(update={"state": "STOPPED"}))
    assert path.read_bytes() == before
    assert list(tmp_path.glob("demo-health-*.tmp")) == []


def test_exported_service_contract_matches_runtime_and_denies_orders():
    from pathlib import Path
    from pydantic import TypeAdapter
    from jsonschema import Draft202012Validator, ValidationError
    from uuid import uuid4

    module = implementation()
    path = Path(__file__).resolve().parents[1] / "docs/schemas/demo-sync-service-health.schema.json"
    assert path.is_file()
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema == TypeAdapter(module.ServiceHealth | module.ServiceHealthView).json_schema()
    health = module.ServiceHealth(
        run_id=uuid4(),
        state="STOPPED",
        ticks=0,
        published_at=NOW,
        valid_until=NOW + timedelta(seconds=30),
    ).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(health)
    health["may_submit_orders"] = True
    with pytest.raises(ValidationError):
        validator.validate(health)


def test_oversize_config_is_rejected_before_validation(tmp_path):
    module = implementation()
    path = tmp_path / "config.json"
    path.write_bytes(b" " * (module.MAX_CONFIG_BYTES + 1))
    with pytest.raises(module.ServiceRejected, match="^CONFIG_INVALID$"):
        module.load_config(path, {})


def test_tick_failure_publishes_fixed_code_without_private_diagnostics(tmp_path):
    module = implementation()

    class BrokenController:
        def tick(self):
            raise ValueError("fixture-private-password")

    result = module.run_service(
        BrokenController(), tmp_path / "health.json", Event(), clock=lambda: NOW
    )
    assert result.state == "HALTED" and result.code == "SERVICE_FAILURE"
    assert "fixture-private-password" not in result.model_dump_json()


def test_package_registers_service_entry_point():
    import tomllib
    from pathlib import Path

    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert (
        project["project"]["scripts"]["ai-quant-demo-sync"] == "ai_quant_os.mt5_sync_service:main"
    )
