"""Hypothesis-library contract: signal invariants for edge_hypotheses.

Every hypothesis signal follows the contract documented in the module
docstring: ``signal(bars, params, carry_diff)`` returns exactly
``len(bars)`` positions, each in ``[-1, 1]``, decided using only data up to
and including bar ``t`` (no lookahead). Carry hypotheses fail safe to
all-zero when ``carry_diff`` is ``None``.
"""

from datetime import datetime, timedelta, timezone

from ai_quant_os.edge_hypotheses import HYPOTHESES, BarsView

_BY_ID = {hypothesis.id: hypothesis for hypothesis in HYPOTHESES}


def _hourly_bars(days=5):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)  # a Monday
    closes = tuple(1.1000 + 0.0001 * t for t in range(24 * days))
    return BarsView(
        instrument_id="EURUSD",
        timestamps=tuple(start + timedelta(hours=t) for t in range(24 * days)),
        opens=closes,
        highs=tuple(close + 0.00005 for close in closes),
        lows=tuple(close - 0.00005 for close in closes),
        closes=closes,
    )


def _daily_bars(count=130):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    closes = tuple(1.1000 + 0.001 * t for t in range(count))
    return BarsView(
        instrument_id="EURUSD",
        timestamps=tuple(start + timedelta(days=t) for t in range(count)),
        opens=closes,
        highs=tuple(close + 0.0005 for close in closes),
        lows=tuple(close - 0.0005 for close in closes),
        closes=closes,
    )


def _assert_signal_contract(hypothesis, bars, carry_diff=None):
    positions = hypothesis.signal(bars, hypothesis.base_params, carry_diff)
    assert len(positions) == len(bars.closes)
    assert all(-1.0 <= position <= 1.0 for position in positions)
    assert all(position in (-1.0, 0.0, 1.0) for position in positions)
    return positions


def test_library_registers_fourteen_unique_hypotheses():
    ids = [hypothesis.id for hypothesis in HYPOTHESES]
    assert len(HYPOTHESES) == 14
    assert len(set(ids)) == 14
    assert {hypothesis.family for hypothesis in HYPOTHESES} == {
        "session_volatility",
        "carry_momentum",
    }
    for hypothesis in HYPOTHESES:
        assert hypothesis.instruments == ("EURUSD", "GBPUSD", "USDJPY", "EURJPY")
        assert hypothesis.base_params
        assert hypothesis.falsification_criterion


def test_signal_contract_session_family():
    bars = _hourly_bars()
    for hypothesis_id in ("S01", "S02", "S03", "S06"):
        _assert_signal_contract(_BY_ID[hypothesis_id], bars)


def test_signal_contract_carry_momentum_family():
    bars = _daily_bars()
    carry = tuple(0.045 for _ in range(len(bars.closes)))
    for hypothesis_id in ("M01", "M02", "M03", "M04", "M05", "M06"):
        hypothesis = _BY_ID[hypothesis_id]
        _assert_signal_contract(hypothesis, bars, carry if hypothesis.uses_carry else None)


def test_carry_hypotheses_fail_safe_without_carry():
    bars = _daily_bars()
    for hypothesis_id in ("M04", "M05"):
        hypothesis = _BY_ID[hypothesis_id]
        assert hypothesis.uses_carry
        assert hypothesis.signal(bars, hypothesis.base_params, None) == (0.0,) * len(bars.closes)


def test_no_lookahead_prefix_invariance():
    bars = _daily_bars()
    prefix = BarsView(
        instrument_id=bars.instrument_id,
        timestamps=bars.timestamps[:80],
        opens=bars.opens[:80],
        highs=bars.highs[:80],
        lows=bars.lows[:80],
        closes=bars.closes[:80],
    )
    # Appending one bar with a huge upward jump must not rewrite history.
    extended = BarsView(
        instrument_id=bars.instrument_id,
        timestamps=bars.timestamps[:81],
        opens=bars.opens[:81],
        highs=bars.highs[:81],
        lows=bars.lows[:81],
        closes=bars.closes[:80] + (bars.closes[80] + 0.5,),
    )
    for hypothesis_id in ("M01", "M03"):
        hypothesis = _BY_ID[hypothesis_id]
        before = hypothesis.signal(prefix, hypothesis.base_params, None)
        after = hypothesis.signal(extended, hypothesis.base_params, None)
        assert after[:80] == before


def test_asia_breakout_takes_long_after_london_break():
    bars = _hourly_bars(days=2)
    positions = _BY_ID["S01"].signal(bars, _BY_ID["S01"].base_params, None)
    # Asia hours (00:00-06:59) on day one are flat: no stance before London.
    assert all(position == 0.0 for position in positions[:7])
    # The steady drift breaks above the Asia range once London opens.
    assert any(position == 1.0 for position in positions[7:21])
