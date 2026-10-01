from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from ai_quant_os.broker import BrokerOutcomeUnknown, BrokerRouter, SimulatedBroker
from ai_quant_os.contracts import TradeIntent
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.paper_state_store import PaperStateStore
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState


def recovery_fixture(tmp_path):
    from ai_quant_os.paper_recovery import PaperRecoveryService

    path = tmp_path / "paper.sqlite3"
    state = PaperStateStore(path)
    risk = RiskKernel(RiskLimits(Decimal(1000), Decimal(1500), 5))
    broker = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True, store_path=path)
    router = BrokerRouter([broker])
    execution = PaperExecutionGateway(
        risk, store_path=path, router=router, on_uncertain_order=state.record_order_uncertainty
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intent = TradeIntent(
        intent_id="recovery-intent",
        strategy_id="demo",
        strategy_version="1",
        instrument_id="EURUSD",
        side="BUY",
        desired_exposure=Decimal(100),
        risk_budget=Decimal(10),
        max_spread=Decimal("0.0003"),
        max_slippage=Decimal("0.0002"),
        valid_until=now + timedelta(minutes=1),
        world_state_version="world-1",
        evidence_refs=("fixture:1",),
        created_at=now,
    )
    market = MarketSnapshot("EURUSD", Decimal("1.1000"), Decimal("1.1001"), now, "world-1", True)
    route = router.route

    def lost_ack(command, *, now):
        route(command, now=now)
        raise BrokerOutcomeUnknown("Injected loss after durable broker acceptance")

    with patch.object(router, "route", side_effect=lost_ack):
        with pytest.raises(BrokerOutcomeUnknown):
            execution.submit(
                intent,
                risk.evaluate(intent, market, now=now),
                idempotency_key="recovery-order",
                now=now,
            )
    return PaperRecoveryService(execution, risk, state), execution, risk, state, broker


def test_recovery_looks_up_once_and_keeps_risk_stopped(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("incident-1", order_id)
    result = service.reconcile("incident-1", order_id, 1)
    assert result["stage"] == "REVIEW_REQUIRED"
    assert result["order_status"] == "ACCEPTED"
    assert result["positions_match"] is True
    assert result["promotion_allowed"] is False
    assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK
    assert len(broker.orders) == 1
    count = state.audit_integrity().event_count
    assert service.reconcile("incident-1", order_id, 1) == result
    assert state.audit_integrity().event_count == count
    assert state.audit_event("paper-recovery:incident-1:attempt:1") is not None


def test_unknown_ack_retains_reservation_and_requires_new_attempt(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("incident-1", order_id)
    with patch.object(broker, "lookup", return_value=None):
        unknown = service.reconcile("incident-1", order_id, 1)
    assert unknown["stage"] == "WAITING_RETRY"
    assert execution.exposure_for("EURUSD").pending_buy_quantity == Decimal(100)
    assert service.reconcile("incident-1", order_id, 1) == unknown
    assert service.reconcile("incident-1", order_id, 2)["stage"] == "REVIEW_REQUIRED"
    assert len(broker.orders) == 1
    assert risk.safety_state == state.safety_state() == SafetyState.NO_NEW_RISK


def test_missing_admission_and_reused_incident_fail_without_order_dispatch(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    with pytest.raises(ValueError, match="contained"):
        service.reconcile("incident-1", order_id, 1)
    service.contain("incident-1", order_id)
    with pytest.raises(ValueError):
        service.contain("incident-1", "missing-order")
    with pytest.raises(ValueError):
        service.reconcile("incident-1", order_id, True)
    assert len(broker.orders) == 1


def test_halt_survives_reconciliation_and_audit_failure_stops_memory(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    risk.set_safety_state(SafetyState.HALT)
    service.contain("incident-1", order_id)
    assert service.reconcile("incident-1", order_id, 1)["safety_state"] == "HALT"
    assert state.safety_state() == SafetyState.HALT
    with patch.object(state, "record_event", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            service.contain("incident-2", order_id)
    assert risk.safety_state == SafetyState.HALT
    with pytest.raises(ValueError):
        risk.recover_paper_safety(before_change=lambda: None)


def test_reconciliation_read_failure_halts_memory(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("incident-1", order_id)
    with patch.object(state, "audit_event", side_effect=ValueError("invalid audit chain")):
        with pytest.raises(ValueError, match="invalid audit"):
            service.reconcile("incident-1", order_id, 1)
    assert risk.safety_state == SafetyState.HALT


def test_unexpected_lookup_failure_halts_memory(tmp_path):
    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    service.contain("incident-1", order_id)
    with patch.object(broker, "lookup", side_effect=RuntimeError("unexpected failure")):
        with pytest.raises(RuntimeError):
            service.reconcile("incident-1", order_id, 1)
    assert risk.safety_state == SafetyState.HALT


def test_broker_fill_mismatch_persists_halt_and_never_imports_fills(tmp_path):
    from ai_quant_os.portfolio import PaperFill

    service, execution, risk, state, broker = recovery_fixture(tmp_path)
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
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source="PAPER_BROKER",
        ),
    )
    service.contain("incident-1", order.order_id)
    result = service.reconcile("incident-1", order.order_id, 1)
    assert result["stage"] == "WAITING_RETRY"
    assert result["positions_match"] is False
    assert risk.safety_state == state.safety_state() == SafetyState.HALT
    assert execution.fills == ()
    assert len(broker.orders) == 1


def test_recovery_containment_holds_kernel_lock_through_persisted_stop(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time

    service, execution, risk, state, broker = recovery_fixture(tmp_path)
    order_id = execution.orders[0].order_id
    execution.reconcile_order(order_id)
    execution.recover_paper_safety(
        before_change=lambda: state.record_recovery(
            "previous", "manual review", {"receipt_id": "previous"}
        )
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
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
        valid_until=now + timedelta(seconds=60),
        world_state_version="world",
        evidence_refs=("fixture",),
        created_at=now,
    )
    quote = MarketSnapshot("EURUSD", Decimal("1.1000"), Decimal("1.1001"), now, "world", True)
    entered, release = Event(), Event()
    record = state.record_order_uncertainty

    def paused_commit(*args):
        record(*args)
        entered.set()
        assert release.wait(5)

    with (
        patch.object(state, "record_order_uncertainty", side_effect=paused_commit),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        stop = pool.submit(service.contain, "new-triage", order_id)
        try:
            assert entered.wait(5)
            approval = pool.submit(risk.evaluate, intent, quote, now=now)
            time.sleep(0.05)
            assert not approval.done()
        finally:
            release.set()
            stop.result(timeout=5)
        assert approval.result(timeout=5).decision == "REJECT"
