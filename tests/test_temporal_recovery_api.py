import asyncio
import time

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("temporalio")
from temporalio.testing import WorkflowEnvironment

from ai_quant_os.api import create_app
from test_paper_recovery import recovery_fixture
from test_temporal_recovery import temporal_binary


def test_actual_application_owns_temporal_recovery_and_restart_reads(tmp_path):
    binary = temporal_binary()

    async def run():
        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order_id = execution.orders[0].order_id
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            target = environment.client.service_client.config.target_host

            def exercise():
                options = dict(
                    operator_token="operator",
                    paper_db_path=tmp_path / "paper.sqlite3",
                    temporal_recovery_enabled=True,
                    temporal_recovery_target=target,
                )
                app = create_app(**options)
                headers = {"X-Operator-Token": "operator"}
                url = "/api/v1/paper/recovery"
                with TestClient(app) as client:
                    assert client.get(url).status_code == 401
                    assert client.get(url, headers=headers).json()["state"] == "READY"
                    payload = {"incident_id": "api-1", "order_id": order_id}
                    assert client.post(url, json=payload).status_code == 401
                    assert (
                        client.post(
                            url, json={**payload, "promotion_allowed": True}, headers=headers
                        ).status_code
                        == 422
                    )
                    assert (
                        client.post(
                            url, json={**payload, "order_id": "absent"}, headers=headers
                        ).status_code
                        == 404
                    )
                    response = client.post(url, json=payload, headers=headers)
                    assert response.status_code == 202
                    assert response.json()["promotion_allowed"] is False
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(url + "/api-1", headers=headers)
                        assert view.status_code == 200
                        if view.json()["state"] == "COMPLETED":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    result = view.json()["result"]
                    assert result["stage"] == "REVIEW_REQUIRED"
                    assert result["order_status"] == "ACCEPTED"
                    assert client.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
                    assert client.post(url, json=payload, headers=headers).status_code == 409
                    assert client.post(url + "/api-1/retry", headers=headers).status_code == 409
                assert app.state.recovery_runtime is None
                with TestClient(create_app(**options)) as client:
                    view = client.get(url + "/api-1", headers=headers).json()
                    assert view["state"] == "COMPLETED"
                    assert view["result"] == result
                    assert client.get(url + "/unknown", headers=headers).status_code == 404
                assert len(broker.orders) == 1

            await asyncio.to_thread(exercise)

    asyncio.run(run())


@pytest.mark.parametrize("health_enabled", [False, True])
def test_unreachable_service_refuses_startup_and_persists_halt(tmp_path, health_enabled):
    import socket
    from ai_quant_os.paper_state_store import PaperStateStore
    from ai_quant_os.risk import SafetyState

    # A bound, non-listening socket holds the port while rejecting RPC connections.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        target = f"127.0.0.1:{unavailable.getsockname()[1]}"
        app = create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            temporal_recovery_enabled=True,
            temporal_recovery_target=target,
            health_watchdog_enabled=health_enabled,
        )
        with pytest.raises(Exception):
            with TestClient(app):
                pytest.fail("Application served despite unreachable required recovery service")
    assert PaperStateStore(tmp_path / "paper.sqlite3").safety_state() == SafetyState.HALT
    assert app.state.recovery_runtime is None
    assert app.state.health_task is None
    assert app.state.graph_runtime is None


def test_actual_worker_fill_mismatch_halts_the_running_api(tmp_path):
    from datetime import datetime, timezone
    from decimal import Decimal
    from ai_quant_os.portfolio import PaperFill

    binary = temporal_binary()

    async def run():
        service, execution, risk, state, broker = recovery_fixture(tmp_path)
        order = execution.orders[0]
        broker.report_fill(
            order.command,
            PaperFill(
                fill_id="broker-only",
                order_id=order.order_id,
                instrument_id="EURUSD",
                side="BUY",
                quantity=Decimal(100),
                price=Decimal("1.1001"),
                fee=Decimal(0),
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                source="PAPER_BROKER",
            ),
        )
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            target = environment.client.service_client.config.target_host

            def exercise():
                app = create_app(
                    operator_token="operator",
                    recovery_token="recovery",
                    paper_db_path=tmp_path / "paper.sqlite3",
                    temporal_recovery_enabled=True,
                    temporal_recovery_target=target,
                )
                headers = {"X-Operator-Token": "operator"}
                url = "/api/v1/paper/recovery"
                with TestClient(app) as client:
                    response = client.post(
                        url,
                        json={"incident_id": "mismatch", "order_id": order.order_id},
                        headers=headers,
                    )
                    assert response.status_code == 202
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(url + "/mismatch", headers=headers).json()
                        if view["stage"] == "WAITING_RETRY":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert client.get("/api/v1/risk").json()["safety_state"] == "HALT"
                    assert state.safety_state().value == "HALT"
                    assert client.get("/api/v1/fills").json()["fills"] == []
                    assert (
                        client.post(
                            url + "/mismatch/retry",
                            json={"stage": "REVIEW_REQUIRED"},
                            headers=headers,
                        ).status_code
                        == 422
                    )
                    assert client.post(url + "/mismatch/retry", headers=headers).status_code == 202
                    recovery_headers = {
                        **headers,
                        "X-Recovery-Token": "recovery",
                        "Idempotency-Key": "blocked-recovery",
                    }
                    assert (
                        client.post(
                            "/api/v1/control/recover-paper",
                            json={"reason": "try recovery"},
                            headers=recovery_headers,
                        ).status_code
                        == 409
                    )
                assert len(broker.orders) == 1

            await asyncio.to_thread(exercise)

    asyncio.run(run())
