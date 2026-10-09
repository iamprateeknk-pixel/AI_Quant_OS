import asyncio
import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def policy(daily=3, monthly=5, daily_reserve=1, monthly_reserve=1):
    assert importlib.util.find_spec("ai_quant_os.agent_call_budgets"), (
        "Missing durable agent call quotas"
    )
    module = importlib.import_module("ai_quant_os.agent_call_budgets")
    return module.AgentCallPolicy(
        tuple(
            module.AgentCallRule(f"A{number:02}", daily, monthly, daily_reserve, monthly_reserve)
            for number in range(1, 19)
        )
    )


def budget(path, declared=None, clock=lambda: NOW):
    module = importlib.import_module("ai_quant_os.agent_call_budgets")
    return module.AgentCallBudget(path, policy=declared, clock=clock)


def test_admission_precedes_handler_and_retains_failed_calls_across_versions(tmp_path):
    declared = policy()
    calls = []

    async def fail(invocation):
        calls.append(invocation)
        raise RuntimeError("private-handler-detail")

    boundary = AgentToolBoundary(
        load_agent_catalog(),
        {"WORLD_STATE_READ": fail},
        call_budget=budget(tmp_path / "budget.sqlite3", declared),
    )

    async def run():
        for version in ("0.1.0", "0.2.0"):
            with pytest.raises(ValueError, match="Agent tool failed"):
                await boundary.call("A02", version, "WORLD_STATE_READ", scope="PAPER", payload={})
        with pytest.raises(PermissionError, match="call budget"):
            await boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={})

    asyncio.run(run())
    assert len(calls) == 2
    reopened = budget(tmp_path / "budget.sqlite3")
    row = next(row for row in reopened.status()["agents"] if row["agent_id"] == "A02")
    assert row["daily_calls"] == 2 and row["daily_remaining"] == 0
    assert row["daily_recovery_reserve"] == 1
    assert reopened.status()["state"] == "ENFORCED"


def test_monthly_ceiling_survives_daily_rollover_and_clock_rollback(tmp_path):
    declared = policy(daily=2, monthly=3, daily_reserve=0, monthly_reserve=1)
    current = [NOW]
    guard = budget(tmp_path / "budget.sqlite3", declared, clock=lambda: current[0])

    async def read(invocation):
        return {"observed": True}

    boundary = AgentToolBoundary(
        load_agent_catalog(), {"WORLD_STATE_READ": read}, call_budget=guard
    )

    async def call():
        return await boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={})

    asyncio.run(call())
    asyncio.run(call())
    current[0] += timedelta(days=1)
    with pytest.raises(PermissionError, match="call budget"):
        asyncio.run(call())
    current[0] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert asyncio.run(call()) == {"observed": True}
    current[0] = NOW
    with pytest.raises(PermissionError, match="call budget"):
        asyncio.run(call())


def test_concurrent_reservations_and_corruption_fail_before_execution(tmp_path):
    declared = policy(daily=2, monthly=10, daily_reserve=1, monthly_reserve=1)
    path = tmp_path / "budget.sqlite3"
    calls = []

    async def read(invocation):
        calls.append(invocation)
        return {}

    def call():
        boundary = AgentToolBoundary(
            load_agent_catalog(), {"WORLD_STATE_READ": read}, call_budget=budget(path, declared)
        )
        try:
            asyncio.run(
                boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={})
            )
            return True
        except PermissionError:
            return False

    budget(path, declared)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: call(), range(2))) == 1
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        db.execute("UPDATE agent_call_reservations SET body=?", (b"forged",))
    assert call() is False
    assert len(calls) == 1
    assert budget(path, declared).status()["state"] == "UNAVAILABLE"


def test_protected_budget_status_and_paper_controls_survive_budget_corruption(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    declared = policy()
    path = tmp_path / "paper.sqlite3"
    app = create_app(
        operator_token="operator",
        paper_db_path=path,
        agent_call_policy=declared,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        url = "/api/v1/agents/call-budget"
        assert client.get(url).status_code == 401
        status = client.get(url, headers={"X-Operator-Token": "operator"}).json()
        assert status["state"] == "ENFORCED" and len(status["agents"]) == 18
        assert status["recovery_spending_enabled"] is False
        with pytest.raises(ValueError, match="Agent tool failed"):
            asyncio.run(
                app.state.graph_runtime._tools.call(
                    "A01", "0.2.0", "TASK_ORCHESTRATE", scope="PAPER", payload={}
                )
            )
        charged = client.get(url, headers={"X-Operator-Token": "operator"}).json()
        assert (
            next(row for row in charged["agents"] if row["agent_id"] == "A01")["daily_calls"] == 1
        )
        pin = tmp_path / "paper.agent-calls.sqlite3.pin.json"
        pin.write_bytes(b"corrupt")
        unavailable = client.get(url, headers={"X-Operator-Token": "operator"}).json()
        assert unavailable["state"] == "UNAVAILABLE" and unavailable["agents"] == []
        assert client.get("/api/v1/system/status").status_code == 200
        response = client.post(
            "/api/v1/control/no-new-risk",
            headers={"X-Operator-Token": "operator", "Idempotency-Key": "budget-emergency"},
            json={"reason": "budget fixture"},
        )
        assert response.status_code == 200
    with TestClient(
        create_app(operator_token="operator", paper_db_path=path, clock=lambda: NOW)
    ) as client:
        assert (
            client.get(url, headers={"X-Operator-Token": "operator"}).json()["state"]
            == "UNAVAILABLE"
        )


def test_real_graph_budget_denial_and_restart_never_repeat_tool(tmp_path):
    from test_graph_runtime import context, RegimeDistribution, NOW as GRAPH_NOW
    from ai_quant_os.graph_runtime import AgentImplementation, GraphRuntime

    declared = policy(daily=1, monthly=4, daily_reserve=0, monthly_reserve=0)

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)
        calls = []

        async def read(invocation):
            calls.append(invocation)
            return {"observed": True}

        async def invoke(invocation):
            await invocation.tools.call("WORLD_STATE_READ", {})
            return RegimeDistribution(label="fixture")

        path = tmp_path / "graph.sqlite3"
        kwargs = {
            "registry": registry,
            "implementations": {
                ("A02", "0.1.0"): AgentImplementation(
                    registry.get("A02", "0.1.0").manifest_ref, RegimeDistribution, invoke
                )
            },
            "tools": AgentToolBoundary(
                registry,
                {"WORLD_STATE_READ": read},
                call_budget=budget(tmp_path / "budget.sqlite3", declared, clock=lambda: GRAPH_NOW),
            ),
            "clock": lambda: GRAPH_NOW,
        }
        async with GraphRuntime.open(path, **kwargs) as runtime:
            assert (await runtime.run("first", evidence)).status == "COMPLETED"
            denied = await runtime.run("second", evidence)
            assert denied.status == "DENIED"
        async with GraphRuntime.open(path, **kwargs) as runtime:
            assert (await runtime.resume("first")).status == "COMPLETED"
            assert await runtime.resume("second") == denied
        assert len(calls) == 1

    asyncio.run(run())


def test_cancelled_admission_never_dispatches_tool_or_blocks_event_loop():
    from threading import Event

    entered, release = Event(), Event()
    calls = []

    class WaitingBudget:
        def reserve(self, invocation):
            entered.set()
            release.wait(0.5)

    async def handler(invocation):
        calls.append(invocation)
        return {}

    async def run():
        boundary = AgentToolBoundary(
            load_agent_catalog(), {"WORLD_STATE_READ": handler}, call_budget=WaitingBudget()
        )
        pending = asyncio.create_task(
            boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={})
        )
        while not entered.is_set():
            await asyncio.sleep(0)
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(run())
    assert calls == []


def test_original_policy_and_database_cannot_be_reset_by_configuration(tmp_path):
    declared = policy()
    path = tmp_path / "budget.sqlite3"
    guard = budget(path, declared)
    assert guard.status()["state"] == "ENFORCED"
    assert budget(path, policy(daily=4, monthly=6)).status()["state"] == "UNAVAILABLE"
    assert budget(path).status()["policy_sha256"] == declared.fingerprint
    path.unlink()
    assert budget(path, declared).status()["state"] == "UNAVAILABLE"
    assert not path.exists()


def test_existing_budget_is_adopted_by_a_previously_disabled_boundary(tmp_path):
    declared = policy(daily=1, monthly=2, daily_reserve=1, monthly_reserve=1)
    path = tmp_path / "budget.sqlite3"
    dormant = budget(path)
    assert dormant.status() == {"state": "DISABLED", "agents": []}
    assert not path.exists()
    budget(path, declared)
    calls = []

    async def read(invocation):
        calls.append(invocation)
        return {}

    boundary = AgentToolBoundary(
        load_agent_catalog(), {"WORLD_STATE_READ": read}, call_budget=dormant
    )
    with pytest.raises(PermissionError, match="call budget"):
        asyncio.run(boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={}))
    assert calls == [] and dormant.status()["state"] == "ENFORCED"


def test_invalid_permissions_and_payloads_never_charge_budget(tmp_path):
    declared = policy()
    guard = budget(tmp_path / "budget.sqlite3", declared)

    async def read(invocation):
        return {}

    boundary = AgentToolBoundary(
        load_agent_catalog(), {"WORLD_STATE_READ": read}, call_budget=guard
    )
    with pytest.raises(PermissionError):
        asyncio.run(boundary.call("A02", "0.2.0", "CANDIDATE_CREATE", scope="SANDBOX", payload={}))
    with pytest.raises(ValueError):
        asyncio.run(
            boundary.call(
                "A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={"agent_id": "A18"}
            )
        )
    assert all(row["daily_calls"] == 0 for row in guard.status()["agents"])


def test_call_policy_rejects_boolean_duplicates_and_missing_roles():
    import json
    from dataclasses import replace
    from ai_quant_os.agent_call_policy import AgentCallPolicy

    declared = policy()
    with pytest.raises(ValueError):
        replace(declared.rules[0], daily_calls=True)
    with pytest.raises(ValueError):
        AgentCallPolicy(declared.rules[:-1])
    with pytest.raises(ValueError):
        AgentCallPolicy((declared.rules[0],) * 18)
    with pytest.raises(ValueError):
        AgentCallPolicy.decode(b'{"schema_version":"1.0","schema_version":"1.0","rules":[]}')
    assert (
        AgentCallPolicy.decode(json.dumps(json.loads(declared.encode()), indent=2).encode())
        == declared
    )


def test_configuration_and_status_contracts(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app
    from ai_quant_os.agent_call_api import AgentCallBudgetStatus
    from ai_quant_os.agent_call_policy import AgentCallPolicy

    declared = policy()
    example = AgentCallPolicy.decode(Path("docs/examples/agent-call-policy.json").read_bytes())
    assert len(example.rules) == 18
    config = tmp_path / "policy.json"
    config.write_bytes(declared.encode())
    monkeypatch.setenv("AI_QUANT_AGENT_CALL_POLICY", str(config))
    with pytest.raises(ValueError):
        create_app(operator_token="operator")
    with TestClient(
        create_app(
            operator_token="operator", paper_db_path=tmp_path / "paper.sqlite3", clock=lambda: NOW
        )
    ) as client:
        result = client.get(
            "/api/v1/agents/call-budget", headers={"X-Operator-Token": "operator"}
        ).json()
        assert result["policy_sha256"] == declared.fingerprint
        assert "tokens" not in result and "cost_usd" not in result
    assert (
        json.loads(Path("docs/schemas/agent-call-budget-status.schema.json").read_text())
        == AgentCallBudgetStatus.model_json_schema()
    )


def test_unreadable_budget_namespace_withholds_tools_without_constructor_failure(
    tmp_path, monkeypatch
):
    from pathlib import Path

    policy()
    target = tmp_path / "budget.sqlite3"
    original = Path.exists

    def denied(path):
        if path == target:
            raise PermissionError("private namespace detail")
        return original(path)

    monkeypatch.setattr(Path, "exists", denied)
    guard = budget(target)
    assert guard.status() == {"state": "UNAVAILABLE", "agents": []}
    calls = []

    async def read(invocation):
        calls.append(invocation)
        return {}

    boundary = AgentToolBoundary(
        load_agent_catalog(), {"WORLD_STATE_READ": read}, call_budget=guard
    )
    with pytest.raises(PermissionError, match="Agent call budget unavailable"):
        asyncio.run(boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={}))
    assert calls == []
