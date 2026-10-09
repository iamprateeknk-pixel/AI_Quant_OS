import importlib.util
import json
from datetime import timedelta
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_mt5_account_sync import NOW
from test_mt5_sync_controller import setup


def implementation():
    assert importlib.util.find_spec("ai_quant_os.mt5_portfolio_observation") is not None
    from ai_quant_os import mt5_portfolio_observation

    return mt5_portfolio_observation


def publication(tmp_path):
    module = implementation()
    _, controller, _, _, _, _, _, _ = setup(tmp_path)
    controller.tick()
    return (
        module,
        controller,
        module.build_portfolio_publication(controller, uuid4(), clock=lambda: NOW),
    )


def test_observation_retains_account_positions_orders_and_original_expiry(tmp_path):
    module, controller, record = publication(tmp_path)
    assert record.state == "OBSERVED" and record.record.account.balance == 10000
    assert record.record.positions[0].quantity_units == 2000
    assert record.record.orders[0].remaining_lots == Decimal("0.02")
    assert record.record.valid_until == NOW + timedelta(seconds=3)
    assert record.record.broker_id == "EXNESS_MT5_DEMO"
    assert record.record.account_freshness == "UNVERIFIED" and record.may_affect_portfolio is False
    raw = record.model_dump_json()
    assert "password" not in raw and "expected_login" not in raw and "terminal_path" not in raw
    assert "private-name" not in raw and "fixture-demo-server" not in raw
    assert controller.inspect_health().last_history_end == NOW


def test_read_does_not_extend_source_expiry_when_frame_is_republished(tmp_path):
    from ai_quant_os.mt5_sync_service import publish_portfolio

    module, controller, first = publication(tmp_path)
    path = tmp_path / "portfolio.json"
    publish_portfolio(path, first)
    assert module.read_portfolio(path, clock=lambda: NOW).state == "AVAILABLE"
    later = module.build_portfolio_publication(
        controller, first.run_id, clock=lambda: NOW + timedelta(seconds=4)
    )
    publish_portfolio(path, later)
    view = module.read_portfolio(path, clock=lambda: NOW + timedelta(seconds=4))
    assert view.state == "STALE" and view.publication.record.valid_until == first.record.valid_until


def test_empty_or_closed_projection_is_explicit_and_contains_no_account_data(tmp_path):
    module = implementation()
    _, controller, _, projection, _, _, calls, _ = setup(tmp_path)
    empty = module.build_portfolio_publication(controller, uuid4(), clock=lambda: NOW)
    assert empty.state == "EMPTY" and empty.record is None and calls == []
    projection.close()
    withheld = module.build_portfolio_publication(controller, uuid4(), clock=lambda: NOW)
    assert withheld.state == "WITHHELD" and withheld.record is None


def test_future_or_oversized_or_duplicate_publication_is_withheld(tmp_path):
    from ai_quant_os.mt5_sync_service import publish_portfolio

    module, _, record = publication(tmp_path)
    path = tmp_path / "portfolio.json"
    publish_portfolio(path, record)
    assert (
        module.read_portfolio(path, clock=lambda: NOW - timedelta(seconds=1)).state == "UNAVAILABLE"
    )
    path.write_bytes(b"x" * 16385)
    assert module.read_portfolio(path, clock=lambda: NOW).state == "UNAVAILABLE"
    path.write_text('{"state":"EMPTY","state":"OBSERVED"}', encoding="utf-8")
    assert module.read_portfolio(path, clock=lambda: NOW).state == "UNAVAILABLE"


def test_service_publishes_observed_portfolio_after_verified_tick(tmp_path):
    from ai_quant_os.mt5_sync_service import run_service

    module = implementation()
    _, controller, _, _, _, time, calls, _ = setup(tmp_path)
    result = run_service(
        controller,
        tmp_path / "health.json",
        Event(),
        max_ticks=1,
        clock=lambda: time[0],
        portfolio_path=tmp_path / "portfolio.json",
    )
    view = module.read_portfolio(tmp_path / "portfolio.json", clock=lambda: time[0])
    assert result.state == "STOPPED" and view.state == "AVAILABLE" and len(calls) == 1
    assert view.publication.run_id == result.run_id


def test_portfolio_publication_failure_stops_future_ticks_and_halts_service(tmp_path, monkeypatch):
    from ai_quant_os import mt5_sync_service

    implementation()
    _, controller, _, _, _, time, calls, _ = setup(tmp_path)

    def fail(*args):
        raise OSError("private storage location")

    monkeypatch.setattr(mt5_sync_service, "publish_portfolio", fail)
    result = mt5_sync_service.run_service(
        controller,
        tmp_path / "health.json",
        Event(),
        max_ticks=2,
        clock=lambda: time[0],
        portfolio_path=tmp_path / "portfolio.json",
    )
    assert result.state == "HALTED" and result.code == "SERVICE_FAILURE" and len(calls) == 1
    assert controller.inspect_health().state == "HEALTHY"


def test_failed_atomic_replacement_preserves_prior_complete_portfolio(tmp_path, monkeypatch):
    from ai_quant_os import mt5_sync_service

    _, _, record = publication(tmp_path)
    path = tmp_path / "portfolio.json"
    mt5_sync_service.publish_portfolio(path, record)
    old = path.read_bytes()

    def fail(*args):
        raise OSError("controlled replacement failure")

    monkeypatch.setattr(mt5_sync_service.os, "replace", fail)
    with pytest.raises(OSError):
        mt5_sync_service.publish_portfolio(path, record)
    assert path.read_bytes() == old and list(tmp_path.glob("demo-health-*.tmp")) == []


def test_demo_portfolio_api_is_owner_protected_and_fixed_source(tmp_path, monkeypatch):
    from ai_quant_os.api import create_app
    from ai_quant_os.mt5_sync_service import publish_portfolio

    _, _, record = publication(tmp_path)
    publish_portfolio(tmp_path / "portfolio.json", record)
    path = "/api/v1/portfolio/demo-observation"
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        assert client.get(path).status_code == 401
        response = client.get(path + "?state_dir=ignored", headers={"X-Operator-Token": "operator"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["source_kind"] == "LOCAL_UNVERIFIED_DEMO_PORTFOLIO"
        assert value["observation"]["publication"]["record"]["account"]["balance"] == "10000.0"
        assert value["may_affect_portfolio"] is False
        paper = client.get("/api/v1/portfolio/positions").json()
        assert paper["mode"] == "PAPER" and all(
            p["net_quantity"] == "0" for p in paper["positions"]
        )
        assert client.post(path, headers={"X-Operator-Token": "operator"}).status_code == 405


def test_portfolio_contracts_are_exported_and_deny_authority(tmp_path):
    from pathlib import Path
    from pydantic import TypeAdapter, ValidationError

    module, _, record = publication(tmp_path)
    from ai_quant_os.mt5_sync_health_api import DemoPortfolioResponse

    for name, model in (
        ("demo-portfolio-publication", module.PortfolioPublication),
        ("demo-portfolio-api", DemoPortfolioResponse),
    ):
        path = Path("docs/schemas") / (name + ".schema.json")
        assert (
            path.is_file()
            and json.loads(path.read_text(encoding="utf-8")) == TypeAdapter(model).json_schema()
        )
    data = record.model_dump(mode="json")
    data["may_affect_portfolio"] = True
    with pytest.raises(ValidationError):
        module.PortfolioPublication.model_validate(data)
    data = record.model_dump(mode="json")
    data["record"]["valid_until"] = (NOW + timedelta(seconds=30)).isoformat()
    with pytest.raises(ValidationError):
        module.PortfolioPublication.model_validate(data)


def test_owner_guard_runs_before_portfolio_file_read(tmp_path, monkeypatch):
    from ai_quant_os.api import create_app
    from ai_quant_os import mt5_sync_health_api

    implementation()
    reads = []
    monkeypatch.setattr(mt5_sync_health_api, "read_portfolio", lambda *a, **k: reads.append(a))
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        assert client.get("/api/v1/portfolio/demo-observation").status_code == 401
        assert (
            client.get(
                "/api/v1/portfolio/demo-observation", headers={"X-Operator-Token": "wrong"}
            ).status_code
            == 401
        )
    assert reads == []


def test_unconfigured_or_missing_portfolio_is_explicit(tmp_path, monkeypatch):
    from ai_quant_os.api import create_app

    implementation()
    monkeypatch.delenv("AI_QUANT_DEMO_SYNC_STATE", raising=False)
    headers = {"X-Operator-Token": "operator"}
    path = "/api/v1/portfolio/demo-observation"
    with TestClient(create_app(operator_token="operator", clock=lambda: NOW)) as client:
        value = client.get(path, headers=headers).json()
        assert value["configured"] is False and value["observation"]["state"] == "UNAVAILABLE"
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        value = client.get(path, headers=headers).json()
        assert value["configured"] is True and value["observation"]["state"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", ["currency", "positions", "orders"])
def test_portfolio_wire_refuses_mixed_currency_or_duplicate_native_identities(tmp_path, change):
    from pydantic import ValidationError

    module, _, record = publication(tmp_path)
    data = record.model_dump(mode="json")
    if change == "currency":
        data["record"]["positions"][0]["currency"] = "EUR"
    else:
        data["record"][change].append(data["record"][change][0].copy())
    with pytest.raises(ValidationError):
        module.PortfolioPublication.model_validate(data)


def test_revoked_owner_session_cannot_read_account_portfolio(tmp_path, monkeypatch):
    from ai_quant_os.api import create_app
    from ai_quant_os import mt5_sync_health_api
    from ai_quant_os.mt5_sync_service import publish_portfolio

    _, _, record = publication(tmp_path)
    publish_portfolio(tmp_path / "portfolio.json", record)
    original = mt5_sync_health_api.read_portfolio
    reads = []

    def observed(*args, **kwargs):
        reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(mt5_sync_health_api, "read_portfolio", observed)
    path = "/api/v1/portfolio/demo-observation"
    with TestClient(
        create_app(operator_token="operator", demo_sync_state_dir=tmp_path, clock=lambda: NOW)
    ) as client:
        login = client.post("/api/v1/operator/sessions", headers={"X-Operator-Token": "operator"})
        headers = {"X-Operator-Session": login.json()["session_token"]}
        assert client.get(path, headers=headers).status_code == 200
        assert client.delete("/api/v1/operator/session", headers=headers).status_code == 200
        assert client.get(path, headers=headers).status_code == 401
    assert len(reads) == 1


def test_real_disabled_service_publishes_empty_portfolio_without_native_sdk(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from test_mt5_sync_service import config_document

    module = implementation()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_document(False)), encoding="utf-8")
    directory = tmp_path / "state"
    environment = {k: v for k, v in os.environ.items() if k.upper() != "AI_QUANT_MT5_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").absolute())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.mt5_sync_service",
            "--config",
            str(config),
            "--state-dir",
            str(directory),
        ],
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    raw = json.loads((directory / "portfolio.json").read_text(encoding="utf-8"))
    assert raw["state"] == "EMPTY" and raw["record"] is None
    assert raw["run_id"] == json.loads(result.stdout)["run_id"]
    assert module.PortfolioPublication.model_validate(raw).may_submit_orders is False
