import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def module():
    assert importlib.util.find_spec("ai_quant_os.realtime_feed") is not None
    from ai_quant_os import realtime_feed

    return realtime_feed


def test_protected_snapshot_matches_authoritative_paper_state_and_restart_epoch():
    module()
    with TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW)) as client:
        assert client.get("/api/v1/realtime/snapshot").status_code == 401
        headers = {"X-Operator-Token": "fixture-token"}
        first = client.get("/api/v1/realtime/snapshot", headers=headers)
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        value = first.json()
        import json
        from pathlib import Path

        fixture = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "apps/web/tests/fixtures/realtime-snapshot.json"
            ).read_text(encoding="utf-8")
        )
        fixture["epoch"] = value["epoch"]
        assert fixture == value
        assert value["mode"] == "PAPER" and value["order_count"] == 0
        assert value["world"]["status"] == "EMPTY"
        assert value["safety_state"] == client.get("/api/v1/system/status").json()["safety_state"]
        next_value = client.get("/api/v1/realtime/snapshot", headers=headers).json()
        assert next_value["epoch"] == value["epoch"] and next_value["sequence"] > value["sequence"]
        halted = client.post(
            "/api/v1/control/no-new-risk",
            headers={**headers, "Idempotency-Key": "feed-control"},
            json={"reason": "feed fixture"},
        )
        assert halted.status_code == 200
        assert (
            client.get("/api/v1/realtime/snapshot", headers=headers).json()["safety_state"]
            == "NO_NEW_RISK"
        )
        halted = client.post(
            "/api/v1/control/halt",
            headers={**headers, "Idempotency-Key": "feed-halt"},
            json={"reason": "feed fixture"},
        )
        assert halted.status_code == 200
        assert (
            client.get("/api/v1/realtime/snapshot", headers=headers).json()["safety_state"]
            == "HALT"
        )
        assert "fixture-token" not in str(value)
    with TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW)) as client:
        assert (
            client.get("/api/v1/realtime/snapshot", headers=headers).json()["epoch"]
            != value["epoch"]
        )


def test_quote_source_expiry_limits_frame_lease_and_clock_regression_withholds():
    module()
    clock = [NOW]
    with TestClient(create_app(operator_token="fixture-token", clock=lambda: clock[0])) as client:
        headers = {"X-Operator-Token": "fixture-token"}
        quote = client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": "1.1",
                "ask": "1.1001",
                "observed_at": (NOW - timedelta(seconds=4)).isoformat(),
                "world_state_version": "feed-fixture",
            },
        )
        assert quote.status_code == 200
        first = client.get("/api/v1/realtime/snapshot", headers=headers).json()
        assert datetime.fromisoformat(
            first["valid_until"].replace("Z", "+00:00")
        ) == NOW + timedelta(seconds=1)
        clock[0] -= timedelta(seconds=1)
        assert client.get("/api/v1/realtime/snapshot", headers=headers).status_code == 503


def test_realtime_order_counter_does_not_load_every_order_and_fill(monkeypatch):
    from ai_quant_os.execution import PaperExecutionGateway

    def expensive_read(self):
        raise AssertionError("Realtime must not materialize the entire order/fill history")

    with TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW)) as client:
        monkeypatch.setattr(PaperExecutionGateway, "orders", property(expensive_read))
        result = client.get(
            "/api/v1/realtime/snapshot", headers={"X-Operator-Token": "fixture-token"}
        )
        assert result.status_code == 200 and result.json()["order_count"] == 0


def test_realtime_order_inventory_survives_restart_and_does_not_count_duplicate_intents(tmp_path):
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "fixture-token", "Idempotency-Key": "feed-order"}
    payload = {
        "intent_id": "feed-order",
        "strategy_id": "demo",
        "strategy_version": "1",
        "instrument_id": "EURUSD",
        "side": "BUY",
        "desired_exposure": "10",
        "risk_budget": "10",
        "max_spread": "0.0003",
        "max_slippage": "0.0002",
        "valid_until": (NOW + timedelta(minutes=1)).isoformat(),
        "world_state_version": "feed-world",
        "evidence_refs": ["fixture:1"],
        "created_at": NOW.isoformat(),
    }
    with TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    ) as client:
        assert (
            client.post(
                "/api/v1/paper/quotes",
                headers=headers,
                json={
                    "instrument_id": "EURUSD",
                    "bid": "1.1",
                    "ask": "1.1001",
                    "observed_at": NOW.isoformat(),
                    "world_state_version": "feed-world",
                },
            ).status_code
            == 200
        )
        first = client.post("/api/v1/paper/intents", headers=headers, json=payload)
        assert first.status_code == 200
        assert (
            client.post("/api/v1/paper/intents", headers=headers, json=payload).json()
            == first.json()
        )
        original = client.get("/api/v1/realtime/snapshot", headers=headers).json()
        assert original["order_count"] == 1
    with TestClient(
        create_app(operator_token="fixture-token", paper_db_path=path, clock=lambda: NOW)
    ) as client:
        restored = client.get("/api/v1/realtime/snapshot", headers=headers).json()
        assert restored["order_count"] == 1 and restored["epoch"] != original["epoch"]


def test_stream_rechecks_authority_before_each_frame_and_releases_capacity():
    m = module()
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    feed = m.RealtimeFeed(lambda now: ("NORMAL", 0, world), clock=lambda: NOW)
    admitted = [True]

    def check():
        if not admitted[0]:
            raise HTTPException(401, "fixture revoked")

    async def probe():
        stream = feed.events(check, frames=2, interval=0)
        first = await anext(stream)
        assert b"event: reset" in first and b"fixture revoked" not in first
        admitted[0] = False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert feed.active_streams == 0

    asyncio.run(probe())


def test_concurrent_stream_limit_and_cancelled_stream_release():
    m = module()
    from ai_quant_os.world_state import WorldStateEngine
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS

    world = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=NOW)
    feed = m.RealtimeFeed(lambda now: ("NORMAL", 0, world), clock=lambda: NOW)

    async def probe():
        streams = [feed.events(lambda: None, frames=2, interval=0) for _ in range(8)]
        for stream in streams:
            await anext(stream)
        extra = feed.events(lambda: None, frames=2, interval=0)
        with pytest.raises(StopAsyncIteration):
            await anext(extra)
        assert feed.active_streams == 8
        for stream in streams:
            await stream.aclose()
        assert feed.active_streams == 0

    asyncio.run(probe())


def test_actual_http_stream_observes_safety_change_then_stops_after_session_revocation(tmp_path):
    import json
    import socket
    import threading
    import time
    import httpx
    import uvicorn

    module()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(
                    operator_token="fixture-token",
                    paper_db_path=tmp_path / "paper.sqlite3",
                    clock=lambda: NOW,
                ),
                log_level="error",
                lifespan="off",
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=4) as client:
                grant = client.post(
                    "/api/v1/operator/sessions", headers={"X-Operator-Token": "fixture-token"}
                )
                assert grant.status_code == 201
                session = {"X-Operator-Session": grant.json()["session_token"]}
                with client.stream("GET", "/api/v1/realtime/events", headers=session) as response:
                    assert (
                        response.status_code == 200
                        and response.headers["cache-control"] == "no-store"
                    )
                    lines = response.iter_lines()

                    def frame():
                        fields = {}
                        for line in lines:
                            if not line and fields:
                                return fields
                            if ": " in line:
                                key, value = line.split(": ", 1)
                                fields[key] = value
                        return None

                    first = frame()
                    assert first["event"] == "reset"
                    original = json.loads(first["data"])
                    assert first["id"] == f"{original['epoch']}:{original['sequence']}"
                    changed = client.post(
                        "/api/v1/control/no-new-risk",
                        headers={
                            **session,
                            "X-Operator-Reauth": "fixture-token",
                            "Idempotency-Key": "stream-change",
                        },
                        json={"reason": "stream fixture"},
                    )
                    assert changed.status_code == 200
                    second = json.loads(frame()["data"])
                    assert (
                        second["sequence"] > original["sequence"]
                        and second["safety_state"] == "NO_NEW_RISK"
                    )
                    assert (
                        client.delete("/api/v1/operator/session", headers=session).status_code
                        == 200
                    )
                    assert frame() is None
                    assert (
                        client.get("/api/v1/realtime/snapshot", headers=session).status_code == 401
                    )
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive(), "Owned test server did not stop"
