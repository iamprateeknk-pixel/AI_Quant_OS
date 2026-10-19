import importlib.util
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.strategy_registry import StrategyRegistry
from ai_quant_os.tick_data import Tick, TickDataset


def module():
    assert importlib.util.find_spec("ai_quant_os.native_tick_portfolio") is not None
    from ai_quant_os import native_tick_portfolio

    return native_tick_portfolio


def inputs(quantity=10):
    implementation = module()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = ("1.1", "1.2", "1.3", "1.2", "1.1", "1.2", "1.3", "1.2")
    return tuple(
        implementation.TickPortfolioInput(
            TickDataset(
                Tick(
                    identifier,
                    "fixture",
                    i,
                    start + timedelta(seconds=2 * i + offset),
                    Decimal(p) + shift,
                    Decimal(p) + shift + Decimal(".0001"),
                    Decimal(1000000),
                    Decimal(1000000),
                )
                for i, p in enumerate(prices)
            ),
            StrategyGenome("tick-momentum@1.0.0", "TICK"),
            quantity,
        )
        for identifier, offset, shift in (("EURUSD", 0, Decimal(0)), ("GBPUSD", 1, Decimal(".2")))
    )


def test_asynchronous_quotes_share_one_native_account_and_reproduce(tmp_path, monkeypatch):
    m = module()
    original, engines = m.BacktestEngine, []

    def observed(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(m, "BacktestEngine", observed)
    source = inputs()
    result = m.run_native_tick_portfolio(
        source, StrategyRegistry(tmp_path / "a"), fee_per_order_usd=Decimal(".10")
    )
    assert (
        len(engines) == 1
        and result.native_order_count == 6
        and result.total_fees_usd == Decimal(".60")
    )
    assert result.net_pnl_usd == Decimal("-10.6050")
    assert all(leg.final_position == 10 for leg in result.legs)
    assert [f.quantity for f in result.legs[0].fills] == [10, 20, 20]
    assert result == m.run_native_tick_portfolio(
        tuple(reversed(source)), StrategyRegistry(tmp_path / "b"), fee_per_order_usd=Decimal(".10")
    )
    m.verify_tick_portfolio_report(result)


def test_partial_tick_liquidity_limits_each_fill_to_displayed_side_size(tmp_path):
    m = module()
    source = inputs()
    limited = tuple(
        replace(
            leg,
            dataset=TickDataset(
                replace(tick, bid_size=Decimal(5), ask_size=Decimal(5))
                for tick in leg.dataset.ticks
            ),
        )
        for leg in source
    )

    report = m.run_native_tick_portfolio(
        limited,
        StrategyRegistry(tmp_path / "limited"),
        fee_per_order_usd=Decimal(".10"),
    )

    assert report.schema_version == "1.1"
    assert report.liquidity_model == "SOURCE_VISIBLE_SIDE_ORDER_SLICES_V1"
    assert report.replay_model == "ASYNC_RECORDED_QUOTE_PORTFOLIO_USD_V2"
    assert any(fill.quantity < Decimal(10) for leg in report.legs for fill in leg.fills)
    assert report.total_fees_usd == Decimal(report.native_order_count) * Decimal(".10")
    for leg in report.legs:
        quotes = {tick.observed_at: tick for tick in leg.source_ticks[1:]}
        for fill in leg.fills:
            quote = quotes[fill.observed_at]
            available = quote.ask_size if fill.side == "BUY" else quote.bid_size
            assert fill.quantity <= available
    m.verify_tick_portfolio_report(report)


def test_partial_tick_report_rejects_fill_above_recorded_side_capacity(tmp_path):
    m = module()
    source = inputs()
    limited = tuple(
        replace(
            leg,
            dataset=TickDataset(
                replace(tick, bid_size=Decimal(5), ask_size=Decimal(5))
                for tick in leg.dataset.ticks
            ),
        )
        for leg in source
    )
    report = m.run_native_tick_portfolio(limited, StrategyRegistry(tmp_path / "tamper"))
    first_leg = report.legs[0]
    first_fill, *remaining_fills = first_leg.fills
    altered = replace(first_fill, quantity=first_fill.quantity + Decimal(1))
    tampered = replace(
        report,
        legs=(replace(first_leg, fills=(altered, *remaining_fills)), report.legs[1]),
    )

    with pytest.raises(ValueError, match="displayed quote-side liquidity"):
        m.verify_tick_portfolio_report(tampered)


def test_shared_collateral_cannot_be_spent_twice(tmp_path):
    m = module()
    with pytest.raises(ValueError, match="collateral|denied|rejected"):
        m.run_native_tick_portfolio(
            inputs(10000),
            StrategyRegistry(tmp_path / "sources"),
            initial_balance_usd=Decimal(20000),
        )


def test_stale_other_leg_quote_refuses_new_exposure(tmp_path):
    m = module()
    source = inputs()
    late = replace(
        source[1],
        dataset=TickDataset(
            replace(t, observed_at=t.observed_at + timedelta(seconds=30))
            for t in source[1].dataset.ticks
        ),
    )
    with pytest.raises(ValueError, match="stale|source|quote"):
        m.run_native_tick_portfolio((source[0], late), StrategyRegistry(tmp_path / "sources"))


@pytest.mark.parametrize("field", ["net_pnl_usd", "total_fees_usd", "native_order_count"])
def test_retained_total_tampering_is_refused(tmp_path, field):
    m = module()
    result = m.run_native_tick_portfolio(inputs(), StrategyRegistry(tmp_path / "sources"))
    with pytest.raises(ValueError):
        m.verify_tick_portfolio_report(replace(result, **{field: 1}))


def test_duplicate_instrument_and_wrong_strategy_are_refused(tmp_path):
    m = module()
    source = inputs()
    registry = StrategyRegistry(tmp_path / "sources")
    with pytest.raises(ValueError):
        m.run_native_tick_portfolio((source[0], source[0]), registry)
    with pytest.raises(ValueError):
        m.run_native_tick_portfolio(
            (replace(source[0], genome=StrategyGenome("moving-average@1.0.0", "BAR")), source[1]),
            registry,
        )


def test_risk_reducing_close_does_not_need_an_unrelated_stale_quote(tmp_path):
    m = module()
    source = inputs()
    start = source[0].dataset.ticks[0].observed_at

    def leg(template, times, prices):
        return replace(
            template,
            dataset=TickDataset(
                replace(
                    template.dataset.ticks[0],
                    sequence=i,
                    observed_at=start + timedelta(seconds=t),
                    bid=Decimal(p),
                    ask=Decimal(p) + Decimal(".0001"),
                )
                for i, (t, p) in enumerate(zip(times, prices, strict=True))
            ),
        )

    a = leg(source[0], (0, 2, 4, 7, 8), ("1.1", "1.2", "1.2", "1.2", "1.2"))
    b = leg(source[1], (1, 2, 3, 9), ("1.3", "1.4", "1.5", "1.5"))
    report = m.run_native_tick_portfolio((a, b), StrategyRegistry(tmp_path / "sources"))
    assert report.legs[0].final_position == 0 and len(report.legs[0].fills) == 2
    assert report.legs[1].final_position == 10


def test_leg_amounts_require_finite_decimal_values(tmp_path):
    m = module()
    report = m.run_native_tick_portfolio(inputs(), StrategyRegistry(tmp_path / "sources"))
    leg = report.legs[0]
    with pytest.raises(ValueError):
        m.verify_tick_portfolio_report(
            replace(report, legs=(replace(leg, final_position=10), report.legs[1]))
        )


def test_jpy_leg_keeps_fees_in_usd_and_uses_signed_final_fx(tmp_path):
    m = module()
    source = inputs()
    jpy = replace(
        source[0],
        dataset=TickDataset(
            replace(t, instrument_id="USDJPY", bid=t.bid * 100, ask=t.bid * 100 + Decimal(".01"))
            for t in source[0].dataset.ticks
        ),
    )
    report = m.run_native_tick_portfolio(
        (jpy, source[1]), StrategyRegistry(tmp_path / "sources"), fee_per_order_usd=Decimal(".10")
    )
    leg = report.legs[1]
    assert leg.instrument_id == "USDJPY" and leg.quote_currency == "JPY"
    assert leg.gross_pnl_quote == Decimal("-500.250")
    assert leg.gross_pnl_usd == leg.gross_pnl_quote / Decimal("120")
    assert leg.fees_usd == Decimal(".30") and report.total_fees_usd == Decimal(".60")
    m.verify_tick_portfolio_report(report)


def test_equal_time_quotes_are_cached_before_orders_and_history_is_prior(tmp_path, monkeypatch):
    m = module()
    source = inputs()
    aligned = replace(
        source[1],
        dataset=TickDataset(
            replace(t, observed_at=t.observed_at - timedelta(seconds=1))
            for t in source[1].dataset.ticks
        ),
    )
    sources = (source[0], aligned)
    checks = []
    original = m._collateral

    def observed(engine, prepared, active, order, fee, age):
        current = engine.cache.quote_tick(active.instrument.id)
        timestamp = active.phases[current.ts_event][1].observed_at
        assert active.history[-1].observed_at < timestamp
        for _, _, metadata, instrument, bridge in prepared:
            quote = engine.cache.quote_tick(instrument.id)
            assert bridge.phases[quote.ts_event][1].observed_at == timestamp
            expected = next(
                row
                for item in sources
                for row in item.dataset.ticks
                if row.instrument_id == metadata.instrument_id and row.observed_at == timestamp
            )
            assert quote.bid_price.as_decimal() == expected.bid
        checks.append(timestamp)
        return original(engine, prepared, active, order, fee, age)

    monkeypatch.setattr(m, "_collateral", observed)
    report = m.run_native_tick_portfolio(sources, StrategyRegistry(tmp_path / "a"))
    assert len(checks) == 6
    assert report == m.run_native_tick_portfolio(
        tuple(reversed(sources)), StrategyRegistry(tmp_path / "b")
    )


def test_forged_fill_price_cannot_be_exported_as_source_bound(tmp_path):
    m = module()
    report = m.run_native_tick_portfolio(inputs(), StrategyRegistry(tmp_path / "sources"))
    leg = report.legs[0]
    wrong = replace(leg.fills[0], price=leg.fills[0].price + Decimal(".00001"))
    with pytest.raises(ValueError, match="source quote"):
        m.verify_tick_portfolio_report(
            replace(report, legs=(replace(leg, fills=(wrong, *leg.fills[1:])), report.legs[1]))
        )
