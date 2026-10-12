"""Protected current native rule observations cannot become trading or recovery authority."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import importlib
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from ai_quant_os.api import create_app

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
URL = "/api/v1/observability/alerts"
HEADERS = {"X-Operator-Token": "fixture-alert-api"}


def module():
    return importlib.import_module("ai_quant_os.monitoring_alerts")


def document(state="pending"):
    production = json.loads(Path("infra/observability/paper-http-alerts.json").read_text())
    rules = []
    for i, rule in enumerate(production["groups"][0]["rules"]):
        rules.append(
            {
                "name": rule["alert"],
                "query": rule["expr"],
                "duration": 120,
                "labels": rule["labels"],
                "annotations": rule["annotations"],
                "health": "ok",
                "lastError": "",
                "lastEvaluation": NOW.isoformat(),
                "state": state if i == 0 else "inactive",
                "alerts": [
                    {
                        "state": state,
                        "activeAt": (
                            NOW - timedelta(seconds=180 if state == "firing" else 30)
                        ).isoformat(),
                        "labels": {
                            "alertname": rule["alert"],
                            "job": "paper_http",
                            "severity": "warning",
                            "instance": "127.0.0.1:8000",
                        },
                        "annotations": rule["annotations"],
                        "value": "0",
                    }
                ]
                if i == 0
                else [],
            }
        )
    return {
        "status": "success",
        "data": {"groups": [{"name": "paper-http-monitoring", "rules": rules}]},
    }


def source(handler=None, enabled=True):
    return module().MonitoringAlertSource(
        enabled=enabled,
        clock=lambda: NOW,
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=document()))
        ),
    )


def test_disabled_no_source_reads_and_auth_before_read():
    calls = []
    owned = source(
        lambda request: calls.append(request) or httpx.Response(200, json=document()), False
    )
    with TestClient(
        create_app(operator_token=HEADERS["X-Operator-Token"], monitoring_alert_source=owned)
    ) as client:
        assert client.get(URL).status_code == 401
        value = client.get(URL, headers=HEADERS)
        assert value.json()["state"] == "DISABLED" and value.json()["rules"] == [] and not calls
        assert value.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("state", ["pending", "firing"])
def test_selected_original_current_observations_no_payload_or_authority(state):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "http://127.0.0.1:9090/api/v1/rules?type=alert"
        assert "x-operator-token" not in request.headers and "cookie" not in request.headers
        value = document(state)
        value["private-payload"] = "private-fixture-never-display"
        return httpx.Response(200, json=value)

    with TestClient(
        create_app(
            operator_token=HEADERS["X-Operator-Token"], monitoring_alert_source=source(handler)
        )
    ) as client:
        assert client.get(URL).status_code == 401 and not calls
        response = client.get(URL, headers=HEADERS)
        value = response.json()
        assert value["state"] == "AVAILABLE" and len(value["rules"]) == 2
        assert value["rules"][0]["state"] == state.upper()
        assert value["rules"][0]["active_since"] == (
            NOW - timedelta(seconds=180 if state == "firing" else 30)
        ).isoformat().replace("+00:00", "Z")
        assert value["notification_verified"] is False and value["durable"] is False
        assert "private-fixture-never-display" not in response.text


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "future",
        "expression",
        "error",
        "duplicate",
        "missing",
        "foreign-label",
        "inactive-alert",
        "firing-too-early",
    ],
)
def test_ambiguous_stale_changed_or_failed_rules_withhold_observations(change):
    value = document()
    rule = value["data"]["groups"][0]["rules"][0]
    if change == "stale":
        rule["lastEvaluation"] = (NOW - timedelta(seconds=46)).isoformat()
    if change == "future":
        rule["lastEvaluation"] = (NOW + timedelta(seconds=1)).isoformat()
    if change == "expression":
        rule["query"] = "vector(0)"
    if change == "error":
        rule["health"] = "err"
        rule["lastError"] = "private-error"
    if change == "duplicate":
        value["data"]["groups"].append(value["data"]["groups"][0])
    if change == "missing":
        value["data"]["groups"][0]["rules"].pop()
    if change == "foreign-label":
        rule["alerts"][0]["labels"]["credential"] = "private-secret"
    if change == "inactive-alert":
        rule["state"] = "inactive"
    if change == "firing-too-early":
        rule["state"] = "firing"
        rule["alerts"][0]["state"] = "firing"
    with TestClient(
        create_app(
            operator_token=HEADERS["X-Operator-Token"],
            monitoring_alert_source=source(lambda request: httpx.Response(200, json=value)),
        )
    ) as client:
        response = client.get(URL, headers=HEADERS)
        assert response.json()["state"] == "UNAVAILABLE" and response.json()["rules"] == []
        assert "private-" not in response.text
        halt = client.post(
            "/api/v1/control/halt",
            headers={**HEADERS, "Idempotency-Key": "monitor-unavailable"},
            json={"reason": "fixture"},
        )
        assert halt.status_code == 200 and halt.json()["safety_state"] == "HALT"


@pytest.mark.parametrize(
    "kind", ["redirect", "oversize", "duplicate-json", "compression", "timeout"]
)
def test_bounded_transport_failures_are_private_and_never_retried(kind):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://private.invalid"})
        if kind == "oversize":
            return httpx.Response(200, content=b"x" * 262145)
        if kind == "duplicate-json":
            return httpx.Response(200, content=b'{"status":"success","status":"error"}')
        if kind == "compression":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"private")
        raise httpx.ReadTimeout("private-transport")

    with TestClient(
        create_app(
            operator_token=HEADERS["X-Operator-Token"], monitoring_alert_source=source(handler)
        )
    ) as client:
        response = client.get(URL, headers=HEADERS)
        assert response.json()["state"] == "UNAVAILABLE" and len(calls) == 1
        assert "private" not in response.text


def test_authorization_checked_again_after_slow_source_read(tmp_path):
    moment = [NOW]

    def handler(request):
        moment[0] += timedelta(minutes=16)
        return httpx.Response(200, json=document())

    app = create_app(
        operator_token=HEADERS["X-Operator-Token"],
        clock=lambda: moment[0],
        paper_db_path=tmp_path / "paper.sqlite3",
        monitoring_alert_source=source(handler),
    )
    with TestClient(app) as client:
        login = client.post("/api/v1/operator/sessions", headers=HEADERS)
        token = login.json()["session_token"]
        assert client.get(URL, headers={"X-Operator-Session": token}).status_code == 401


def test_actual_async_read_deadline_cancellation_and_concurrency_bound():
    async def exercise():
        entered, peak = 0, 0

        async def healthy(request):
            nonlocal entered, peak
            entered += 1
            peak = max(peak, entered)
            try:
                await asyncio.sleep(0.02)
                return httpx.Response(200, json=document())
            finally:
                entered -= 1

        owned = source()
        owned.transport = httpx.MockTransport(healthy)
        results = await asyncio.gather(*(owned.observe() for _ in range(12)))
        assert peak == 4 and all(result.state == "AVAILABLE" for result in results)
        waiting = asyncio.Event()

        async def stalled(request):
            await waiting.wait()
            return httpx.Response(200, json=document())

        owned.transport = httpx.MockTransport(stalled)
        result = await asyncio.wait_for(owned.observe(), 4)
        assert result.state == "UNAVAILABLE" and not result.rules
        task = asyncio.create_task(owned.observe())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owned.transport = httpx.MockTransport(healthy)
        assert all(
            value.state == "AVAILABLE"
            for value in await asyncio.gather(*(owned.observe() for _ in range(4)))
        )

    asyncio.run(exercise())


def test_exported_schema_matches_selected_contract_and_all_source_states():
    from jsonschema import Draft202012Validator

    schema = json.loads(Path("docs/schemas/monitoring-observation.schema.json").read_text())
    assert schema == module().MonitoringObservation.model_json_schema()
    validate = Draft202012Validator(schema).validate
    for state in ("DISABLED", "UNAVAILABLE"):
        validate(
            module().MonitoringObservation(observed_at=NOW, state=state).model_dump(mode="json")
        )
    validate(asyncio.run(source().observe()).model_dump(mode="json"))
