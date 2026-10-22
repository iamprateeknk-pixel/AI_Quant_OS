import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app


def test_recovery_routes_default_disabled_and_protected():
    with TestClient(create_app(operator_token="operator")) as client:
        url = "/api/v1/paper/recovery"
        assert client.get(url).status_code == 401
        headers = {"X-Operator-Token": "operator"}
        assert client.get(url, headers=headers).json()["state"] == "DISABLED"
        assert (
            client.post(
                url, json={"incident_id": "one", "order_id": "order"}, headers=headers
            ).status_code
            == 503
        )
        assert client.post(url + "/one/retry", headers=headers).status_code == 503


@pytest.mark.parametrize(
    "options",
    [
        {"temporal_recovery_enabled": True},
        {"temporal_recovery_enabled": "true"},
        {"temporal_recovery_enabled": True, "temporal_recovery_target": "remote.example:7233"},
        {"temporal_recovery_enabled": True, "temporal_recovery_target": "127.0.0.1:0"},
    ],
)
def test_recovery_configuration_fails_closed(tmp_path, options):
    with pytest.raises(ValueError):
        create_app(operator_token="operator", paper_db_path=tmp_path / "paper.sqlite3", **options)


def test_manual_normal_recovery_waits_for_durable_triage_review(tmp_path):
    from test_paper_recovery import recovery_fixture

    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("first", order_id)
    service.reconcile("first", order_id, 1)
    service.contain("unfinished", order_id)
    options = dict(
        operator_token="operator",
        recovery_token="recovery",
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    headers = {
        "X-Operator-Token": "operator",
        "X-Recovery-Token": "recovery",
        "Idempotency-Key": "manual-recovery",
    }
    with TestClient(create_app(**options)) as client:
        response = client.post(
            "/api/v1/control/recover-paper", json={"reason": "manual review"}, headers=headers
        )
        assert response.status_code == 409
        assert client.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
    service.reconcile("unfinished", order_id, 1)
    with TestClient(create_app(**options)) as client:
        response = client.post(
            "/api/v1/control/recover-paper", json={"reason": "manual review"}, headers=headers
        )
        assert response.status_code == 200
        # Staged recovery: the accepted recovery lands at REDUCED_RISK
        # (canary stage); NORMAL requires the promotion step.
        assert response.json()["safety_state"] == "REDUCED_RISK"
        assert response.json()["stage"] == "CANARY"
