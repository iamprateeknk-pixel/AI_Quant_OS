import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState
from test_graph_runtime import context, open_runtime


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def worker(
    tmp_path, *, registry=None, failures=lambda cursor: (), quotes=lambda: (), clock=lambda: NOW
):
    from ai_quant_os.health_store import HealthStore
    from ai_quant_os.health_watchdog import HealthWatchdog

    state = PaperStateStore(tmp_path / "paper.sqlite3")
    risk = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    execution = PaperExecutionGateway(risk, store_path=tmp_path / "paper.sqlite3")
    registry = registry or load_agent_catalog()
    store = HealthStore(tmp_path / "health.sqlite3")
    value = HealthWatchdog(store, state, risk, execution, registry, failures, quotes, clock)
    value.restore()
    return value, store, state, risk, registry


def test_real_failed_graph_is_quarantined_and_restored_by_exact_version(tmp_path):
    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)

        async def broken(invocation):
            raise RuntimeError("injected mission failure")

        async with await open_runtime(tmp_path / "graph.sqlite3", registry, broken) as runtime:
            receipt = await runtime.run("broken-1", evidence)
            assert receipt.status == "FAILED"
            value, store, state, risk, _ = worker(
                tmp_path, registry=registry, failures=runtime.health_receipts
            )
            value.step()
            assert registry.lifecycle("A02", "0.1.0") == "QUARANTINED"
            assert registry.lifecycle("A02", "0.2.0") == "SHADOW"
            with pytest.raises(PermissionError):
                registry.require_tool("A02", "0.1.0", "WORLD_STATE_READ", scope="PAPER")
            with pytest.raises(PermissionError):
                registry.set_lifecycle("A02", "0.1.0", "ACTIVE")
            assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK
            assert len(store.incidents()) == 1
            assert store.incidents()[0].code == "AGENT_FAILED"
            value.step()
            assert len(store.incidents()) == 1
        value, store, state, risk, restored = worker(tmp_path)
        assert restored.lifecycle("A02", "0.1.0") == "QUARANTINED"
        assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK

    asyncio.run(run())


@pytest.mark.parametrize("seconds, code", [(6, "QUOTE_STALE"), (-1, "QUOTE_FUTURE")])
def test_stale_quote_is_journaled_once_and_cannot_auto_normalize(tmp_path, seconds, code):
    quote = MarketSnapshot("EURUSD", Decimal("1.10"), Decimal("1.11"), NOW, "world-1", True)
    value, store, state, risk, registry = worker(
        tmp_path, quotes=lambda: (quote,), clock=lambda: NOW + timedelta(seconds=seconds)
    )
    value.step()
    assert store.incidents()[0].code == code
    assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK
    value.step()
    assert len(store.incidents()) == 1
    assert all(
        registry.lifecycle(m.agent_id, m.agent_version) == "SHADOW" for m in registry.manifests()
    )
    assert value.status()["open_incidents"] == 1


def test_corrupt_paper_audit_is_recorded_in_independent_journal(tmp_path):
    value, store, state, risk, registry = worker(tmp_path)
    state.record_event("before", {"action": "fixture"})
    with sqlite3.connect(tmp_path / "paper.sqlite3") as connection:
        connection.execute("UPDATE audit_events SET event_json = '{}' WHERE event_key = 'before'")
    value.step()
    assert risk.safety_state == SafetyState.HALT
    assert store.incidents()[0].code == "AUDIT_INVALID"
    assert store.integrity().valid
    assert not state.audit_integrity().valid


def test_tampered_health_journal_refuses_restore(tmp_path):
    quote = MarketSnapshot("EURUSD", Decimal(1), Decimal(1), NOW, "world", False)
    value, store, state, risk, registry = worker(tmp_path, quotes=lambda: (quote,))
    value.step()
    with sqlite3.connect(tmp_path / "health.sqlite3") as connection:
        connection.execute("UPDATE health_incidents SET incident_json = '{}'")
    with pytest.raises(ValueError, match="integrity|canonical|binding"):
        worker(tmp_path)


def test_immediate_agent_observation_storage_failure_persists_halt(tmp_path):
    from unittest.mock import patch

    async def run():
        registry = load_agent_catalog()
        evidence = await context(registry)

        async def broken(invocation):
            raise RuntimeError("injected mission failure")

        async with await open_runtime(tmp_path / "graph.sqlite3", registry, broken) as runtime:
            receipt = await runtime.run("broken-storage", evidence)
        value, store, state, risk, registry = worker(tmp_path, registry=registry)
        with patch.object(store, "record", side_effect=OSError("disk failure")):
            with pytest.raises(OSError):
                value.observe_receipt(receipt)
        assert risk.safety_state == state.safety_state() == SafetyState.HALT
        assert registry.lifecycle("A02", "0.1.0") == "QUARANTINED"

    asyncio.run(run())


@pytest.mark.parametrize("raise_after", [False, True])
def test_owned_health_call_drains_state_work_after_repeated_cancellation(tmp_path, raise_after):
    from threading import Event
    from ai_quant_os import health_watchdog

    async def run():
        value, store, state, risk, registry = worker(tmp_path)
        entered, release = Event(), Event()

        def blocked_check():
            result = value.step()
            entered.set()
            assert release.wait(5)
            if raise_after:
                raise OSError("injected late failure")
            return result

        task = asyncio.create_task(health_watchdog.owned_health_call(blocked_check))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.05)
                assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_graph_scan_reaches_failures_after_healthy_page_and_checks_corruption(tmp_path):
    from ai_quant_os.context_checkpoints import ContextCheckpoint
    from ai_quant_os.graph_ledger import GraphLedger

    async def seed():
        registry = load_agent_catalog()
        evidence = await context(registry)
        return ContextCheckpoint(
            schema_version="1.0", manifest=registry.get("A02", "0.1.0"), context=evidence
        )

    ledger = GraphLedger(tmp_path / "graph.sqlite3")
    checkpoint = asyncio.run(seed())
    try:
        for index in range(70):
            ledger.create(f"run-{index:03d}", checkpoint)
        ledger.create("run-070", checkpoint)
        ledger.transition("run-070", "CREATED", "RUNNING")
        ledger.transition("run-070", "RUNNING", "FAILED")
        value, store, state, risk, registry = worker(tmp_path, failures=ledger.health_receipts)
        value.step()
        assert value.pending_graph_scan is True
        assert store.incidents() == ()
        value.step()
        assert value.pending_graph_scan is False
        assert store.incidents()[0].code == "AGENT_FAILED"
        assert registry.lifecycle("A02", "0.1.0") == "QUARANTINED"
        with sqlite3.connect(tmp_path / "graph.sqlite3") as connection:
            connection.execute("UPDATE graph_runs SET record_json = '{}' WHERE run_id = 'run-000'")
        value.step()
        assert risk.safety_state == state.safety_state() == SafetyState.HALT
        assert {i.code for i in store.incidents()} == {"AGENT_FAILED", "GRAPH_INTEGRITY_FAILED"}
    finally:
        ledger.close()


def test_journal_capacity_failure_halts_both_current_and_durable_state(tmp_path):
    observations = [MarketSnapshot("EURUSD", Decimal(1), Decimal(1), NOW, "world-1", False)]
    value, store, state, risk, registry = worker(tmp_path, quotes=lambda: tuple(observations))
    store.capacity = 1
    value.step()
    observations.append(MarketSnapshot("GBPUSD", Decimal(1), Decimal(1), NOW, "world-2", False))
    with pytest.raises(ValueError, match="capacity"):
        value.step()
    assert risk.safety_state == state.safety_state() == SafetyState.HALT
    assert len(store.incidents()) == 1
    assert store.integrity().valid


def test_watchdog_retains_actual_unknown_order_and_halts_actual_fill_mismatch(tmp_path):
    from test_paper_recovery import recovery_fixture
    from ai_quant_os.health_store import HealthStore
    from ai_quant_os.health_watchdog import HealthWatchdog
    from ai_quant_os.portfolio import PaperFill

    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    store = HealthStore(tmp_path / "health.sqlite3")
    value = HealthWatchdog(
        store,
        state,
        risk,
        execution,
        load_agent_catalog(),
        lambda cursor: (),
        lambda: (),
        lambda: NOW,
    )
    value.step()
    assert {i.code for i in store.incidents()} == {"BROKER_UNCONFIRMED"}
    assert execution.exposure_for("EURUSD").pending_buy_quantity == Decimal(100)
    order = execution.orders[0]
    broker.report_fill(
        order.command,
        PaperFill(
            fill_id="unimported",
            order_id=order.order_id,
            instrument_id="EURUSD",
            side="BUY",
            quantity=Decimal(100),
            price=Decimal("1.1001"),
            fee=Decimal(0),
            observed_at=NOW,
            source="PAPER_BROKER",
        ),
    )
    value.step()
    assert {i.code for i in store.incidents()} == {"BROKER_UNCONFIRMED", "POSITION_MISMATCH"}
    assert risk.safety_state == state.safety_state() == SafetyState.HALT
    assert execution.orders[0].status == "UNKNOWN"
    assert execution.fills == ()
    assert len(broker.orders) == 1


def test_clock_regression_halts_without_fabricating_clock_measurement(tmp_path):
    now = [NOW]
    value, store, state, risk, registry = worker(tmp_path, clock=lambda: now[0])
    value.step()
    now[0] -= timedelta(seconds=1)
    value.step()
    assert store.incidents()[0].code == "CLOCK_REGRESSION"
    assert risk.safety_state == state.safety_state() == SafetyState.HALT


def test_persistent_health_stop_holds_kernel_authority_through_audit_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import patch
    import time
    from ai_quant_os.contracts import TradeIntent

    bad_quote = MarketSnapshot("EURUSD", Decimal(1), Decimal(1), NOW, "world", False)
    value, store, state, risk, registry = worker(tmp_path, quotes=lambda: (bad_quote,))
    intent = TradeIntent(
        intent_id="during-stop",
        strategy_id="demo",
        strategy_version="1",
        instrument_id="EURUSD",
        side="BUY",
        desired_exposure=Decimal(100),
        risk_budget=Decimal(10),
        max_spread=Decimal("0.0003"),
        max_slippage=Decimal("0.0002"),
        valid_until=NOW + timedelta(seconds=60),
        world_state_version="world",
        evidence_refs=("fixture",),
        created_at=NOW,
    )
    valid_quote = MarketSnapshot("EURUSD", Decimal("1.1000"), Decimal("1.1001"), NOW, "world", True)
    entered, release = Event(), Event()
    record = state.record_control

    def paused_commit(*args):
        record(*args)
        entered.set()
        assert release.wait(5)

    with (
        patch.object(state, "record_control", side_effect=paused_commit),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        check = pool.submit(value.step)
        try:
            assert entered.wait(5)
            approval = pool.submit(risk.evaluate, intent, valid_quote, now=NOW)
            time.sleep(0.05)
            assert not approval.done()
        finally:
            release.set()
            check.result(timeout=5)
        assert approval.result(timeout=5).decision == "REJECT"
