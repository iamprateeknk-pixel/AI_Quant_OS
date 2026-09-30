from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from dataclasses import replace
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st
from fastapi.testclient import TestClient

from ai_quant_os.currency_portfolio import CurrencyInstrument, FxRate
from ai_quant_os.currency_risk import CurrencyReservation, CurrencyRiskContext, CurrencyRiskPolicy
from ai_quant_os.contracts import TradeIntent
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.risk import RiskKernel, RiskLimits
from ai_quant_os.order_store import PaperOrderStore
from ai_quant_os.execution import PaperExecutionGateway
from ai_quant_os.api import create_app
from ai_quant_os.currency_portfolio import CurrencyPendingOrder
from ai_quant_os.portfolio import PaperFill


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
POLICY = CurrencyRiskPolicy("USD", Decimal("2000"), Decimal("4000"), 5)


def trade(quantity="1000"):
    return TradeIntent(
        "currency-intent",
        "fixture",
        "1",
        "EURUSD",
        "BUY",
        Decimal(quantity),
        Decimal("10"),
        Decimal("0.001"),
        Decimal("0.0002"),
        NOW + timedelta(minutes=1),
        "world-1",
        ("fixture:1",),
        NOW,
    )


def context(*, age=0):
    return CurrencyRiskContext(
        (),
        (),
        (CurrencyInstrument("EURUSD", "EUR", "USD"),),
        (
            FxRate(
                "EUR", "USD", Decimal("2.2"), Decimal("2.2001"), NOW - timedelta(seconds=age), True
            ),
        ),
    )


def test_currency_policy_blocks_high_notional_within_unit_limit():
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5), currency_policy=POLICY)
    quote = MarketSnapshot("EURUSD", Decimal("2.2"), Decimal("2.2001"), NOW, "world-1", True)
    decision = risk.evaluate(
        trade(), quote, now=NOW, portfolio=PortfolioExposure.empty(), currency_context=context()
    )
    assert decision.decision == "REJECT"
    assert "CURRENCY_EXPOSURE_LIMIT" in decision.violations
    assert "GROSS_CURRENCY_EXPOSURE_LIMIT" in decision.violations


def test_configured_currency_gate_cannot_be_omitted_or_use_expired_rates():
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5), currency_policy=POLICY)
    quote = MarketSnapshot("EURUSD", Decimal("2.2"), Decimal("2.2001"), NOW, "world-1", True)
    missing = risk.evaluate(trade("100"), quote, now=NOW)
    assert missing.decision == "REJECT"
    assert "CURRENCY_CONTEXT_REQUIRED" in missing.violations
    stale = risk.evaluate(trade("100"), quote, now=NOW, currency_context=context(age=6))
    assert stale.decision == "REJECT"
    assert "STALE_FX_RATE" in stale.violations


def test_reservation_transaction_independently_rejects_currency_overage(tmp_path):
    store = PaperOrderStore(tmp_path / "paper.sqlite3")
    order = {
        "order_id": "over-cap",
        "intent_id": "over-cap",
        "status": "PENDING",
        "command": {"instrument_id": "EURUSD", "side": "BUY", "quantity": "1000"},
    }
    with pytest.raises(ValueError, match="CURRENCY_EXPOSURE_LIMIT"):
        store.insert(
            "over-cap",
            "over-cap",
            json.dumps({"max_slippage": "0.0002"}),
            json.dumps(order),
            "over-cap",
            expected_snapshot_id=store.exposure_snapshot().snapshot_id,
            max_portfolio_exposure=Decimal("1500"),
            currency_reservation=CurrencyReservation(POLICY, context()),
            now=NOW,
        )
    assert store.all_orders() == ()


def test_prices_expiring_after_reservation_block_dispatch_and_allow_checked_retry(tmp_path):
    time = [NOW]
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5), currency_policy=POLICY)
    gateway = PaperExecutionGateway(
        risk, store_path=tmp_path / "paper.sqlite3", clock=lambda: time[0]
    )
    quote = MarketSnapshot("EURUSD", Decimal("2.2"), Decimal("2.2001"), NOW, "world-1", True)
    intent = trade("100")
    approval = risk.evaluate(
        intent, quote, now=NOW, portfolio=gateway.exposure_snapshot, currency_context=context()
    )
    original_insert = gateway._store.insert

    def delayed_insert(*args, **kwargs):
        inserted = original_insert(*args, **kwargs)
        time[0] = NOW + timedelta(seconds=6)
        return inserted

    with patch.object(gateway._store, "insert", side_effect=delayed_insert):
        with pytest.raises(ValueError, match="STALE_FX_RATE"):
            gateway.submit(intent, approval, idempotency_key="delayed", now=NOW)
    assert gateway.orders[0].status == "PENDING"
    assert gateway._router.lookup(gateway.orders[0].command) is None
    fills, pending = gateway.currency_ledger
    fresh_context = replace(
        context(),
        fills=fills,
        pending=pending,
        rates=(replace(context().rates[0], observed_at=time[0]),),
    )
    recovered = gateway.recover_pending(
        intent,
        idempotency_key="delayed",
        market=replace(quote, observed_at=time[0]),
        now=time[0],
        currency_context=fresh_context,
    )
    assert recovered.status == "ACCEPTED"
    assert len(gateway.orders) == 1


def test_concurrent_reservations_cannot_exceed_currency_cap(tmp_path):
    path = tmp_path / "paper.sqlite3"
    quote = MarketSnapshot("EURUSD", Decimal("2.2"), Decimal("2.2001"), NOW, "world-1", True)
    gateways = [
        PaperExecutionGateway(
            RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5), currency_policy=POLICY),
            store_path=path,
        )
        for _ in range(2)
    ]
    intents = [replace(trade("600"), intent_id=f"concurrent-{index}") for index in range(2)]
    approvals = [
        gateway._risk.evaluate(
            intent, quote, now=NOW, portfolio=gateway.exposure_snapshot, currency_context=context()
        )
        for gateway, intent in zip(gateways, intents)
    ]

    def submit(index):
        try:
            return gateways[index].submit(
                intents[index], approvals[index], idempotency_key=f"key-{index}", now=NOW
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(submit, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(gateways[0].orders) == 1
    fills, pending = gateways[0].currency_ledger
    decision = gateways[0]._risk.evaluate(
        replace(trade("600"), intent_id="retry"),
        quote,
        now=NOW,
        portfolio=gateways[0].exposure_snapshot,
        currency_context=replace(context(), fills=fills, pending=pending),
    )
    assert decision.decision == "REJECT"
    assert "CURRENCY_EXPOSURE_LIMIT" in decision.violations
    assert "PORTFOLIO_EXPOSURE_LIMIT" not in decision.violations


def test_api_enforces_fixed_currency_caps_before_creating_second_order():
    client = TestClient(create_app(operator_token="fixture-token", clock=lambda: NOW))
    headers = {"X-Operator-Token": "fixture-token", "Idempotency-Key": "first"}
    assert (
        client.post(
            "/api/v1/paper/quotes",
            headers=headers,
            json={
                "instrument_id": "EURUSD",
                "bid": "2.2",
                "ask": "2.2001",
                "observed_at": NOW.isoformat(),
                "world_state_version": "world-1",
            },
        ).status_code
        == 200
    )
    payload = json.loads(json.dumps(asdict(trade("900")), default=str))
    assert (
        client.post("/api/v1/paper/intents", headers=headers, json=payload).json()["status"]
        == "ACCEPTED"
    )
    second = {**payload, "intent_id": "second", "desired_exposure": "100"}
    rejected = client.post(
        "/api/v1/paper/intents", headers={**headers, "Idempotency-Key": "second"}, json=second
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECT"
    assert len(client.get("/api/v1/orders").json()["orders"]) == 1
    assert "CURRENCY_EXPOSURE_LIMIT" in client.get("/api/v1/audit").json()["events"][-1]["reason"]
    limits = client.get("/api/v1/risk").json()
    assert limits["max_currency_exposure_reporting"] == "2000"
    assert limits["max_gross_currency_exposure_reporting"] == "4000"


def test_an_existing_overage_allows_only_orders_that_do_not_increase_it():
    filled = PaperFill(
        "filled", "filled", "EURUSD", "BUY", Decimal("1000"), Decimal("2.2"), Decimal("1"), NOW
    )
    before = replace(context(), fills=(filled,))
    reducing = CurrencyPendingOrder("reduce", "EURUSD", "SELL", Decimal("100"))
    increasing = CurrencyPendingOrder("increase", "EURUSD", "BUY", Decimal("100"))
    assert POLICY.violations(before, reducing, now=NOW) == ()
    assert "CURRENCY_EXPOSURE_LIMIT" in POLICY.violations(before, increasing, now=NOW)


def test_currency_prices_and_metadata_cannot_disagree_with_primary_market_policy():
    risk = RiskKernel(RiskLimits(Decimal("1000"), Decimal("1500"), 5), currency_policy=POLICY)
    quote = MarketSnapshot("EURUSD", Decimal("2.2"), Decimal("2.2001"), NOW, "world-1", True)
    low_rate = replace(
        context(), rates=(replace(context().rates[0], bid=Decimal("1.1"), ask=Decimal("1.1001")),)
    )
    assert (
        "CURRENCY_MARKET_MISMATCH"
        in risk.evaluate(trade("100"), quote, now=NOW, currency_context=low_rate).violations
    )
    changed_metadata = replace(context(), instruments=(CurrencyInstrument("EURUSD", "GBP", "USD"),))
    assert (
        "CURRENCY_INSTRUMENT_POLICY_MISMATCH"
        in risk.evaluate(trade("100"), quote, now=NOW, currency_context=changed_metadata).violations
    )


@given(
    quantity=st.integers(1, 1000), price_cents=st.integers(100, 500), reserved=st.integers(0, 100)
)
def test_approved_buy_reservations_obey_currency_and_gross_caps(quantity, price_cents, reserved):
    ask = Decimal(price_cents) / 100 + Decimal("0.0001")
    before = replace(
        context(),
        rates=(replace(context().rates[0], bid=ask - Decimal("0.0001"), ask=ask),),
        pending=(
            CurrencyPendingOrder("existing", "EURUSD", "BUY", Decimal(reserved), Decimal("0.0002")),
        )
        if reserved
        else (),
    )
    candidate = CurrencyPendingOrder(
        "candidate", "EURUSD", "BUY", Decimal(quantity), Decimal("0.0002")
    )
    if not POLICY.violations(before, candidate, now=NOW):
        total_quantity = Decimal(reserved + quantity)
        eur_usd_value = total_quantity * ask
        usd_cash_bound = total_quantity * (ask + Decimal("0.0002"))
        assert eur_usd_value <= Decimal("2000")
        assert usd_cash_bound <= Decimal("2000")
        assert eur_usd_value + usd_cash_bound <= Decimal("4000")
