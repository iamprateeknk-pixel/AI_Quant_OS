"""V3.2 section 28: the global safety ladder has six rungs.

Regression tests for the audit gap where the kernel only implemented
NORMAL / NO_NEW_RISK / FLATTEN_ONLY / HALT while the blueprint (and the
Mission Control operator console) require CAUTION and REDUCED_RISK too.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ai_quant_os.contracts import TradeIntent
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.risk import RiskKernel, RiskLimits, SafetyState

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _kernel() -> RiskKernel:
    return RiskKernel(
        RiskLimits(
            max_order_exposure=Decimal("1000"),
            max_portfolio_exposure=Decimal("1500"),
            max_quote_age_seconds=5,
        )
    )


def _intent() -> TradeIntent:
    return TradeIntent(
        intent_id="intent-1",
        strategy_id="demo",
        strategy_version="1",
        instrument_id="EURUSD",
        side="BUY",
        desired_exposure=Decimal("100"),
        risk_budget=Decimal("10"),
        max_spread=Decimal("0.0003"),
        max_slippage=Decimal("0.0002"),
        valid_until=NOW + timedelta(minutes=1),
        world_state_version="world-1",
        evidence_refs=("fixture:1",),
        created_at=NOW,
    )


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id="EURUSD",
        bid=Decimal("1.1000"),
        ask=Decimal("1.1001"),
        observed_at=NOW - timedelta(seconds=1),
        world_state_version="world-1",
        trusted=True,
    )


def test_ladder_order_matches_blueprint():
    assert [state.value for state in SafetyState] == [
        "NORMAL",
        "CAUTION",
        "REDUCED_RISK",
        "NO_NEW_RISK",
        "FLATTEN_ONLY",
        "HALT",
    ]


def test_escalation_is_monotonic_across_new_rungs():
    kernel = _kernel()
    kernel.set_safety_state(SafetyState.CAUTION)
    kernel.set_safety_state(SafetyState.REDUCED)
    kernel.set_safety_state(SafetyState.NO_NEW_RISK)
    assert kernel.safety_state is SafetyState.NO_NEW_RISK
    try:
        kernel.set_safety_state(SafetyState.CAUTION)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("de-escalation without gated recovery must fail")


def test_caution_still_permits_new_risk():
    kernel = _kernel()
    kernel.set_safety_state(SafetyState.CAUTION)
    decision = kernel.evaluate(_intent(), _market(), now=NOW)
    assert decision.decision == "APPROVE", decision.violations


def test_reduced_refuses_new_risk():
    kernel = _kernel()
    kernel.set_safety_state(SafetyState.REDUCED)
    decision = kernel.evaluate(_intent(), _market(), now=NOW)
    assert decision.decision == "REJECT"
    assert "REDUCED_RISK" in decision.violations


def test_recovery_allowed_from_reduced_and_caution_but_not_halt():
    for state in (SafetyState.CAUTION, SafetyState.REDUCED, SafetyState.NO_NEW_RISK):
        kernel = _kernel()
        kernel.set_safety_state(state)
        kernel.recover_paper_safety(before_change=lambda: None)
        assert kernel.safety_state is SafetyState.NORMAL

    kernel = _kernel()
    kernel.set_safety_state(SafetyState.HALT)
    try:
        kernel.recover_paper_safety(before_change=lambda: None)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("HALT must not recover through the paper shortcut")
