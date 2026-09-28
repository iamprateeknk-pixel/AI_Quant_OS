from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st

from ai_quant_os.contracts import RiskDecision, TradeIntent
from ai_quant_os.broker import (
    BrokerAck,
    BrokerOutcomeUnknown,
    BrokerRouter,
    BrokerUnavailable,
    SimulatedBroker,
)
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def intent(*, exposure: str = "100", intent_id: str = "intent-1") -> TradeIntent:
    return TradeIntent(
        intent_id=intent_id,
        strategy_id="demo",
        strategy_version="1",
        instrument_id="EURUSD",
        side="BUY",
        desired_exposure=Decimal(exposure),
        risk_budget=Decimal("10"),
        max_spread=Decimal("0.0003"),
        max_slippage=Decimal("0.0002"),
        valid_until=NOW + timedelta(minutes=1),
        world_state_version="world-1",
        evidence_refs=("fixture:1",),
        created_at=NOW,
    )


def market(*, age_seconds: int = 1, spread: str = "0.0001") -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id="EURUSD",
        bid=Decimal("1.1000"),
        ask=Decimal("1.1000") + Decimal(spread),
        observed_at=NOW - timedelta(seconds=age_seconds),
        world_state_version="world-1",
        trusted=True,
    )


def kernel() -> RiskKernel:
    return RiskKernel(
        RiskLimits(
            max_order_exposure=Decimal("1000"),
            max_portfolio_exposure=Decimal("1500"),
            max_quote_age_seconds=5,
        )
    )


def test_stale_market_rejects_new_risk() -> None:
    decision = kernel().evaluate(intent(), market(age_seconds=6), now=NOW)
    assert decision.decision == "REJECT"
    assert "STALE_MARKET" in decision.violations


def test_untrusted_market_rejects_new_risk() -> None:
    quote = market()
    quote = MarketSnapshot(**{**quote.__dict__, "trusted": False})
    decision = kernel().evaluate(intent(), quote, now=NOW)
    assert decision.decision == "REJECT"


def test_limit_rejects_oversized_intent() -> None:
    decision = kernel().evaluate(intent(exposure="1001"), market(), now=NOW)
    assert decision.decision == "REJECT"
    assert "ORDER_EXPOSURE_LIMIT" in decision.violations


def test_portfolio_limit_rejects_new_risk_using_named_snapshot() -> None:
    exposure = PortfolioExposure(
        "paper:observed", "EURUSD", Decimal("1200"), Decimal("0"), Decimal("0")
    )
    decision = kernel().evaluate(intent(exposure="400"), market(), now=NOW, portfolio=exposure)
    assert decision.decision == "REJECT"
    assert "PORTFOLIO_EXPOSURE_LIMIT" in decision.violations
    assert decision.portfolio_snapshot_id == "paper:observed"


def test_risk_reducing_order_can_fit_portfolio_limit() -> None:
    exposure = PortfolioExposure(
        "paper:observed", "EURUSD", Decimal("1200"), Decimal("0"), Decimal("0")
    )
    sell = replace(intent(exposure="400"), side="SELL")
    decision = kernel().evaluate(sell, market(), now=NOW, portfolio=exposure)
    assert decision.decision == "APPROVE"


def test_risk_reducing_order_does_not_get_blocked_by_existing_overage() -> None:
    exposure = PortfolioExposure(
        "paper:over-limit", "EURUSD", Decimal("1600"), Decimal("0"), Decimal("0")
    )
    sell = replace(intent(exposure="400"), side="SELL")
    decision = kernel().evaluate(sell, market(), now=NOW, portfolio=exposure)
    assert decision.decision == "APPROVE"


@given(
    st.integers(min_value=-2000, max_value=2000),
    st.integers(min_value=0, max_value=1000),
    st.integers(min_value=0, max_value=1000),
    st.integers(min_value=1, max_value=1000),
    st.sampled_from(["BUY", "SELL"]),
)
def test_approved_intents_never_increase_an_over_limit_worst_case(
    filled: int, pending_buy: int, pending_sell: int, quantity: int, side: str
) -> None:
    exposure = PortfolioExposure(
        "paper:property", "EURUSD", Decimal(filled), Decimal(pending_buy), Decimal(pending_sell)
    )
    trade = replace(intent(exposure=str(quantity)), side=side)
    decision = kernel().evaluate(trade, market(), now=NOW, portfolio=exposure)
    if decision.decision == "APPROVE":
        after = exposure.with_order(side, Decimal(quantity))
        assert after <= max(Decimal("1500"), exposure.worst_case_quantity)


@given(st.decimals(min_value="1000.000001", max_value="1000000", places=6))
def test_any_exposure_above_fixed_ceiling_is_rejected(exposure: Decimal) -> None:
    decision = kernel().evaluate(intent(exposure=str(exposure)), market(), now=NOW)
    assert decision.decision == "REJECT"
    assert "ORDER_EXPOSURE_LIMIT" in decision.violations


@given(st.decimals(min_value="0.000001", max_value="1000", places=6))
def test_any_positive_exposure_within_ceiling_passes_limit(exposure: Decimal) -> None:
    decision = kernel().evaluate(intent(exposure=str(exposure)), market(), now=NOW)
    assert decision.decision == "APPROVE"
    assert "ORDER_EXPOSURE_LIMIT" not in decision.violations


def test_safety_halt_blocks_new_risk() -> None:
    risk = kernel()
    risk.set_safety_state(SafetyState.HALT)
    decision = risk.evaluate(intent(), market(), now=NOW)
    assert decision.decision == "HALT"


def test_approved_intent_creates_one_paper_order_under_retry() -> None:
    risk = kernel()
    decision = risk.evaluate(intent(), market(), now=NOW)
    gateway = PaperExecutionGateway(risk)

    first = gateway.submit(intent(), decision, idempotency_key="request-1", now=NOW)
    second = gateway.submit(intent(), decision, idempotency_key="request-1", now=NOW)

    assert first.order_id == second.order_id
    assert len(gateway.orders) == 1


def test_risk_approval_cannot_fund_two_distinct_orders() -> None:
    trade = intent()
    risk = kernel()
    decision = risk.evaluate(trade, market(), now=NOW)
    gateway = PaperExecutionGateway(risk)
    first = gateway.submit(trade, decision, idempotency_key="first", now=NOW)
    with pytest.raises(ValueError, match="intent.*already"):
        gateway.submit(trade, decision, idempotency_key="second", now=NOW)
    assert first is not None
    assert len(gateway.orders) == 1


def test_intent_id_cannot_create_second_order_with_new_decision(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    first = risk.evaluate(trade, market(), now=NOW)
    gateway.submit(trade, first, idempotency_key="first", now=NOW)
    second = risk.evaluate(trade, market(), now=NOW)
    with pytest.raises(ValueError, match="intent.*already"):
        gateway.submit(trade, second, idempotency_key="second", now=NOW)

    restarted_risk = kernel()
    restarted = PaperExecutionGateway(restarted_risk, store_path=path)
    with pytest.raises(ValueError, match="intent.*already"):
        restarted.submit(
            trade,
            restarted_risk.evaluate(trade, market(), now=NOW),
            idempotency_key="third",
            now=NOW,
        )


def test_parallel_gateways_do_not_route_duplicate_intent(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    simulator = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    router = BrokerRouter([simulator])
    first_risk, second_risk = kernel(), kernel()
    first = PaperExecutionGateway(first_risk, store_path=path, router=router)
    second = PaperExecutionGateway(second_risk, store_path=path, router=router)

    def submit(gateway: PaperExecutionGateway, risk: RiskKernel, key: str) -> str:
        try:
            result = gateway.submit(
                trade, risk.evaluate(trade, market(), now=NOW), idempotency_key=key, now=NOW
            )
            return result.order_id
        except ValueError:
            return "REJECTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda args: submit(*args),
                [(first, first_risk, "one"), (second, second_risk, "two")],
            )
        )
    assert outcomes.count("REJECTED") == 1
    assert len(simulator.orders) == 1
    assert len(first.orders) == 1


def test_exposure_snapshot_tracks_pending_and_filled_units_across_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent(exposure="1000")
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    empty = gateway.exposure_snapshot
    assert empty.snapshot_id == "paper:empty"
    order = gateway.submit(
        trade,
        risk.evaluate(trade, market(), now=NOW, portfolio=empty),
        idempotency_key="exposure-order",
        now=NOW,
    )
    pending = gateway.exposure_snapshot
    assert pending.snapshot_id != empty.snapshot_id
    assert pending.pending_buy_quantity == Decimal("1000")
    assert pending.filled_net_quantity == Decimal("0")

    gateway.record_fill(
        fill_id="exposure-fill",
        order_id=order.order_id,
        quantity=Decimal("1000"),
        price=Decimal("1.10"),
        fee=Decimal("0"),
        observed_at=NOW,
    )
    filled = gateway.exposure_snapshot
    assert filled.snapshot_id != pending.snapshot_id
    assert filled.pending_buy_quantity == Decimal("0")
    assert filled.filled_net_quantity == Decimal("1000")
    assert PaperExecutionGateway(kernel(), store_path=path).exposure_snapshot == filled


def test_concurrent_distinct_orders_cannot_cross_portfolio_limit(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    simulator = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    router = BrokerRouter([simulator])
    first_risk, second_risk = kernel(), kernel()
    first = PaperExecutionGateway(first_risk, store_path=path, router=router)
    second = PaperExecutionGateway(second_risk, store_path=path, router=router)
    snapshot = first.exposure_snapshot
    trade_a = intent(exposure="1000", intent_id="a")
    trade_b = intent(exposure="1000", intent_id="b")
    decision_a = first_risk.evaluate(trade_a, market(), now=NOW, portfolio=snapshot)
    decision_b = second_risk.evaluate(trade_b, market(), now=NOW, portfolio=snapshot)
    assert decision_a.decision == decision_b.decision == "APPROVE"

    def submit(gateway, trade, decision, key):
        try:
            gateway.submit(trade, decision, idempotency_key=key, now=NOW)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: submit(*args),
                [(first, trade_a, decision_a, "a"), (second, trade_b, decision_b, "b")],
            )
        )
    assert results.count(True) == 1
    assert len(simulator.orders) == 1
    assert first.exposure_snapshot.worst_case_quantity == Decimal("1000")


def test_parallel_gateways_replay_same_idempotency_key(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    simulator = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    router = BrokerRouter([simulator])
    first_risk, second_risk = kernel(), kernel()
    first = PaperExecutionGateway(first_risk, store_path=path, router=router)
    second = PaperExecutionGateway(second_risk, store_path=path, router=router)
    trade = intent(exposure="1000")
    snapshot = first.exposure_snapshot
    decisions = [
        risk.evaluate(trade, market(), now=NOW, portfolio=snapshot)
        for risk in (first_risk, second_risk)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda args: args[0].submit(trade, args[1], idempotency_key="same-key", now=NOW),
                [(first, decisions[0]), (second, decisions[1])],
            )
        )
    assert receipts[0].order_id == receipts[1].order_id
    assert len(simulator.orders) == 1


def test_corrupt_fill_side_cannot_understate_exposure(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent(exposure="100")
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    order = gateway.submit(
        trade,
        risk.evaluate(trade, market(), now=NOW, portfolio=gateway.exposure_snapshot),
        idempotency_key="tamper",
        now=NOW,
    )
    gateway.record_fill(
        fill_id="tampered-fill",
        order_id=order.order_id,
        quantity=Decimal("100"),
        price=Decimal("1.10"),
        fee=Decimal("0"),
        observed_at=NOW,
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT fill_json FROM paper_fills WHERE fill_id = ?", ("tampered-fill",)
        ).fetchone()
        changed = json.loads(row[0])
        changed["side"] = "SELL"
        connection.execute(
            "UPDATE paper_fills SET fill_json = ? WHERE fill_id = ?",
            (json.dumps(changed), "tampered-fill"),
        )
    with pytest.raises(ValueError, match="Fill does not match"):
        gateway.exposure_snapshot


def test_rejected_risk_cannot_create_order() -> None:
    risk = kernel()
    decision = risk.evaluate(intent(), market(age_seconds=10), now=NOW)
    gateway = PaperExecutionGateway(risk)
    outcome = gateway.submit(intent(), decision, idempotency_key="request-1", now=NOW)
    assert outcome is None
    assert not gateway.orders


def test_reconciliation_mismatch_forces_no_new_risk() -> None:
    risk = kernel()
    gateway = PaperExecutionGateway(risk)
    gateway.reconcile(local_positions={"EURUSD": Decimal("100")}, broker_positions={})
    assert risk.safety_state == SafetyState.NO_NEW_RISK


def test_mismatched_idempotency_payload_is_rejected() -> None:
    risk = kernel()
    gateway = PaperExecutionGateway(risk)
    original = intent()
    gateway.submit(
        original, risk.evaluate(original, market(), now=NOW), idempotency_key="same", now=NOW
    )
    changed = intent(exposure="200", intent_id="intent-2")
    decision = risk.evaluate(changed, market(), now=NOW)
    try:
        gateway.submit(changed, decision, idempotency_key="same", now=NOW)
    except ValueError as error:
        assert "idempotency" in str(error)
    else:
        raise AssertionError("Different intent reused an idempotency key")


def test_same_intent_id_with_changed_exposure_cannot_reuse_key() -> None:
    risk = kernel()
    gateway = PaperExecutionGateway(risk)
    original = intent()
    gateway.submit(
        original, risk.evaluate(original, market(), now=NOW), idempotency_key="same", now=NOW
    )
    changed = intent(exposure="200")
    decision = risk.evaluate(changed, market(), now=NOW)
    try:
        gateway.submit(changed, decision, idempotency_key="same", now=NOW)
    except ValueError as error:
        assert "idempotency" in str(error)
    else:
        raise AssertionError("Changed exposure reused an idempotency key")


def test_parallel_retries_create_one_order() -> None:
    trade = intent()
    risk = kernel()
    decision = risk.evaluate(trade, market(), now=NOW)
    gateway = PaperExecutionGateway(risk)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: gateway.submit(trade, decision, idempotency_key="parallel", now=NOW),
                range(32),
            )
        )
    assert len({result.order_id for result in results}) == 1
    assert len(gateway.orders) == 1


def test_forged_risk_decision_cannot_create_order() -> None:
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk)
    forged = RiskDecision(
        risk_id="forged",
        intent_id=trade.intent_id,
        decision="APPROVE",
        approved_exposure=trade.desired_exposure,
        rules_evaluated=(),
        violations=(),
        portfolio_snapshot_id="paper:empty",
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="Risk decision"):
        gateway.submit(trade, forged, idempotency_key="forged", now=NOW)


def test_halt_after_approval_prevents_order() -> None:
    trade = intent()
    risk = kernel()
    decision = risk.evaluate(trade, market(), now=NOW)
    risk.set_safety_state(SafetyState.HALT)
    gateway = PaperExecutionGateway(risk)
    assert gateway.submit(trade, decision, idempotency_key="halted", now=NOW) is None


def test_order_retry_survives_gateway_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    decision = risk.evaluate(trade, market(), now=NOW)
    first = PaperExecutionGateway(risk, store_path=path).submit(
        trade, decision, idempotency_key="restart", now=NOW
    )

    restarted = PaperExecutionGateway(kernel(), store_path=path)
    second = restarted.submit(trade, decision, idempotency_key="restart", now=NOW)
    assert second.order_id == first.order_id
    assert len(restarted.orders) == 1


def test_changed_payload_after_restart_cannot_reuse_key(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    risk = kernel()
    original = intent()
    PaperExecutionGateway(risk, store_path=path).submit(
        original, risk.evaluate(original, market(), now=NOW), idempotency_key="restart", now=NOW
    )

    restarted = PaperExecutionGateway(kernel(), store_path=path)
    with pytest.raises(ValueError, match="idempotency"):
        restarted.submit(
            intent(exposure="200"),
            risk.evaluate(intent(exposure="200"), market(), now=NOW),
            idempotency_key="restart",
            now=NOW,
        )


@pytest.mark.parametrize("field", ["risk_budget", "max_spread", "max_slippage"])
def test_nonfinite_intent_bounds_are_rejected(field: str) -> None:
    with pytest.raises(ValueError):
        replace(intent(), **{field: Decimal("NaN")})


def test_nonfinite_risk_ceiling_is_rejected() -> None:
    with pytest.raises(ValueError):
        RiskLimits(
            max_order_exposure=Decimal("NaN"),
            max_portfolio_exposure=Decimal("1500"),
            max_quote_age_seconds=5,
        )


def test_disabled_paper_adapter_cannot_accept_gateway_order() -> None:
    risk = kernel()
    trade = intent()
    router = BrokerRouter([SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=False)])
    gateway = PaperExecutionGateway(risk, router=router)
    with pytest.raises(BrokerUnavailable):
        gateway.submit(
            trade,
            risk.evaluate(trade, market(), now=NOW),
            idempotency_key="blocked",
            now=NOW,
        )
    assert gateway.orders[0].status == "REJECTED"
    assert gateway.exposure_snapshot.worst_case_quantity == Decimal("0")


def test_rejected_paper_broker_ack_does_not_persist_accepted_order() -> None:
    class RejectingBroker(SimulatedBroker):
        def place(self, command):
            return BrokerAck("PAPER", "reject-1", "EURUSD", "REJECTED")

    risk = kernel()
    trade = intent()
    broker = RejectingBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    gateway = PaperExecutionGateway(risk, router=BrokerRouter([broker]))
    with pytest.raises(BrokerUnavailable, match="rejected"):
        gateway.submit(
            trade,
            risk.evaluate(trade, market(), now=NOW),
            idempotency_key="broker-rejected",
            now=NOW,
        )
    assert gateway.orders[0].status == "REJECTED"
    assert gateway.exposure_snapshot.worst_case_quantity == Decimal("0")


def test_unrecognized_broker_ack_keeps_exposure_reserved(tmp_path) -> None:
    class AmbiguousBroker(SimulatedBroker):
        def place(self, command):
            return BrokerAck("PAPER", "ack-1", "EURUSD", "PENDING")

    risk = kernel()
    trade = intent()
    gateway = PaperExecutionGateway(
        risk,
        store_path=tmp_path / "paper.sqlite3",
        router=BrokerRouter([AmbiguousBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)]),
    )
    with pytest.raises(BrokerOutcomeUnknown):
        gateway.submit(
            trade,
            risk.evaluate(trade, market(), now=NOW),
            idempotency_key="ambiguous",
            now=NOW,
        )
    assert gateway.orders[0].status == "UNKNOWN"
    assert gateway.exposure_snapshot.pending_buy_quantity == trade.desired_exposure
    assert risk.safety_state == SafetyState.NO_NEW_RISK


def test_order_reservation_is_committed_before_broker_call(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"

    class ObservingBroker(SimulatedBroker):
        observed_status: str | None = None

        def place(self, command):
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT order_json FROM paper_orders WHERE order_id = ?", (command.order_id,)
                ).fetchone()
            self.observed_status = json.loads(row[0])["status"] if row else None
            return super().place(command)

    broker = ObservingBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    risk = kernel()
    trade = intent()
    gateway = PaperExecutionGateway(risk, store_path=path, router=BrokerRouter([broker]))
    order = gateway.submit(
        trade,
        risk.evaluate(trade, market(), now=NOW, portfolio=gateway.exposure_snapshot),
        idempotency_key="durable",
        now=NOW,
    )
    assert broker.observed_status == "DISPATCHING"
    assert order.status == "ACCEPTED"


def test_accepted_order_retains_broker_acknowledgement_after_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    order = gateway.submit(
        trade,
        risk.evaluate(trade, market(), now=NOW),
        idempotency_key="ack",
        now=NOW,
    )
    expected = f"PAPER:{order.order_id}"
    assert order.broker_order_id == expected
    restarted = PaperExecutionGateway(kernel(), store_path=path)
    assert restarted.replay(trade, idempotency_key="ack").broker_order_id == expected


def test_uncertain_broker_outcome_holds_exposure_and_blocks_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"

    class UncertainBroker(SimulatedBroker):
        calls = 0

        def place(self, command):
            self.calls += 1
            super().place(command)
            raise RuntimeError("ack lost")

    broker = UncertainBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    trade = intent(exposure="1000")
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path, router=BrokerRouter([broker]))
    with pytest.raises(BrokerOutcomeUnknown):
        gateway.submit(
            trade,
            risk.evaluate(trade, market(), now=NOW, portfolio=gateway.exposure_snapshot),
            idempotency_key="uncertain",
            now=NOW,
        )
    assert gateway.orders[0].status == "UNKNOWN"
    assert gateway.exposure_snapshot.pending_buy_quantity == Decimal("1000")
    assert risk.safety_state == SafetyState.NO_NEW_RISK

    restarted_risk = kernel()
    restarted = PaperExecutionGateway(
        restarted_risk, store_path=path, router=BrokerRouter([broker])
    )
    assert restarted_risk.safety_state == SafetyState.NO_NEW_RISK
    assert (
        restarted.submit(
            trade,
            restarted_risk.evaluate(trade, market(), now=NOW),
            idempotency_key="uncertain",
            now=NOW,
        ).status
        == "UNKNOWN"
    )
    assert broker.calls == 1


def test_restart_does_not_resend_order_left_dispatching(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"

    class InterruptedBroker(SimulatedBroker):
        calls = 0

        def place(self, command):
            self.calls += 1
            raise KeyboardInterrupt()

    broker = InterruptedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path, router=BrokerRouter([broker]))
    with pytest.raises(KeyboardInterrupt):
        gateway.submit(
            trade,
            risk.evaluate(trade, market(), now=NOW),
            idempotency_key="interrupted",
            now=NOW,
        )
    assert gateway.orders[0].status == "DISPATCHING"

    restarted_risk = kernel()
    restarted = PaperExecutionGateway(
        restarted_risk, store_path=path, router=BrokerRouter([broker])
    )
    assert restarted_risk.safety_state == SafetyState.NO_NEW_RISK
    assert restarted.replay(trade, idempotency_key="interrupted").status == "DISPATCHING"
    assert broker.calls == 1


def test_reserved_order_can_be_dispatched_once_after_restart(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent(exposure="1000")
    risk = kernel()
    first = PaperExecutionGateway(risk, store_path=path)
    with patch.object(first._store, "transition", side_effect=RuntimeError("interrupted")):
        with pytest.raises(RuntimeError, match="interrupted"):
            first.submit(
                trade,
                risk.evaluate(trade, market(), now=NOW),
                idempotency_key="pending",
                now=NOW,
            )
    assert first.orders[0].status == "PENDING"

    simulator = SimulatedBroker("PAPER", {"EURUSD": "EURUSD"}, enabled=True)
    restarted = PaperExecutionGateway(kernel(), store_path=path, router=BrokerRouter([simulator]))
    recovered = restarted.recover_pending(
        trade,
        idempotency_key="pending",
        market=market(),
        now=NOW,
    )
    assert recovered.status == "ACCEPTED"
    assert recovered.broker_order_id == f"PAPER:{recovered.order_id}"
    assert (
        restarted.recover_pending(
            trade,
            idempotency_key="pending",
            market=market(),
            now=NOW,
        )
        == recovered
    )
    assert len(simulator.orders) == 1


def test_pending_recovery_requires_fresh_market_and_unexpired_intent(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    first = PaperExecutionGateway(risk, store_path=path)
    with patch.object(first._store, "transition", side_effect=RuntimeError("interrupted")):
        with pytest.raises(RuntimeError):
            first.submit(
                trade,
                risk.evaluate(trade, market(), now=NOW),
                idempotency_key="pending",
                now=NOW,
            )

    restarted = PaperExecutionGateway(kernel(), store_path=path)
    with pytest.raises(BrokerUnavailable, match="market"):
        restarted.recover_pending(
            trade,
            idempotency_key="pending",
            market=market(age_seconds=10),
            now=NOW,
        )
    assert restarted.orders[0].status == "PENDING"
    expired = restarted.recover_pending(
        trade,
        idempotency_key="pending",
        market=market(),
        now=NOW + timedelta(minutes=2),
    )
    assert expired.status == "REJECTED"
    assert restarted.exposure_snapshot.worst_case_quantity == Decimal("0")


def test_partial_fills_persist_and_cannot_overfill_order(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent(exposure="100")
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    order = gateway.submit(
        trade, risk.evaluate(trade, market(), now=NOW), idempotency_key="fill-order", now=NOW
    )
    first = gateway.record_fill(
        fill_id="fill-1",
        order_id=order.order_id,
        quantity=Decimal("60"),
        price=Decimal("1.10"),
        fee=Decimal("0.10"),
        observed_at=NOW,
    )
    assert gateway.orders[0].status == "PARTIALLY_FILLED"
    partial_exposure = gateway.exposure_snapshot
    assert partial_exposure.filled_net_quantity == Decimal("60")
    assert partial_exposure.pending_buy_quantity == Decimal("40")
    assert partial_exposure.worst_case_quantity == Decimal("100")
    assert (
        gateway.record_fill(
            fill_id="fill-1",
            order_id=order.order_id,
            quantity=Decimal("60"),
            price=Decimal("1.10"),
            fee=Decimal("0.10"),
            observed_at=NOW,
        )
        == first
    )
    with pytest.raises(ValueError, match="exceeds order quantity"):
        gateway.record_fill(
            fill_id="fill-2",
            order_id=order.order_id,
            quantity=Decimal("41"),
            price=Decimal("1.10"),
            fee=Decimal("0"),
            observed_at=NOW,
        )
    second = gateway.record_fill(
        fill_id="fill-2",
        order_id=order.order_id,
        quantity=Decimal("40"),
        price=Decimal("1.10"),
        fee=Decimal("0"),
        observed_at=NOW,
    )
    assert second.quantity == Decimal("40")
    restarted = PaperExecutionGateway(kernel(), store_path=path)
    assert len(restarted.fills) == 2
    assert restarted.orders[0].status == "FILLED"
    assert restarted.replay(trade, idempotency_key="fill-order").status == "FILLED"


def test_changed_fill_retry_is_rejected(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    order = gateway.submit(
        trade, risk.evaluate(trade, market(), now=NOW), idempotency_key="fill-order", now=NOW
    )
    gateway.record_fill(
        fill_id="fill-1",
        order_id=order.order_id,
        quantity=Decimal("10"),
        price=Decimal("1.10"),
        fee=Decimal("0"),
        observed_at=NOW,
    )
    with pytest.raises(ValueError, match="fill id reused"):
        PaperExecutionGateway(kernel(), store_path=path).record_fill(
            fill_id="fill-1",
            order_id=order.order_id,
            quantity=Decimal("11"),
            price=Decimal("1.10"),
            fee=Decimal("0"),
            observed_at=NOW,
        )


def test_parallel_fill_reports_cannot_exceed_order_quantity(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent(exposure="100")
    risk = kernel()
    first = PaperExecutionGateway(risk, store_path=path)
    order = first.submit(
        trade, risk.evaluate(trade, market(), now=NOW), idempotency_key="fill-order", now=NOW
    )
    second = PaperExecutionGateway(kernel(), store_path=path)

    def report(gateway: PaperExecutionGateway, fill_id: str) -> bool:
        try:
            gateway.record_fill(
                fill_id=fill_id,
                order_id=order.order_id,
                quantity=Decimal("60"),
                price=Decimal("1.10"),
                fee=Decimal("0"),
                observed_at=NOW,
            )
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: report(*args), [(first, "a"), (second, "b")]))
    assert results.count(True) == 1
    assert sum((fill.quantity for fill in first.fills), Decimal(0)) == Decimal("60")


def test_fill_outside_intent_lifetime_is_rejected(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    trade = intent()
    risk = kernel()
    gateway = PaperExecutionGateway(risk, store_path=path)
    order = gateway.submit(
        trade, risk.evaluate(trade, market(), now=NOW), idempotency_key="lifetime", now=NOW
    )
    with pytest.raises(ValueError, match="before intent"):
        gateway.record_fill(
            fill_id="early",
            order_id=order.order_id,
            quantity=Decimal("1"),
            price=Decimal("1.10"),
            fee=Decimal("0"),
            observed_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="after order expiry"):
        gateway.record_fill(
            fill_id="late",
            order_id=order.order_id,
            quantity=Decimal("1"),
            price=Decimal("1.10"),
            fee=Decimal("0"),
            observed_at=NOW + timedelta(minutes=2),
        )
