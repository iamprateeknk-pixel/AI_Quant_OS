from dataclasses import replace
from decimal import Decimal, localcontext
import importlib.util

import pytest

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.tick_data import TickDataset
from test_nautilus_ticks import run, ticks


def verifier():
    assert importlib.util.find_spec("ai_quant_os.native_tick_evidence") is not None
    from ai_quant_os.native_tick_evidence import verify_native_tick_result

    return verify_native_tick_result


def test_actual_native_tick_result_reconciles_prices_fees_cash_and_short_reversal():
    verify = verifier()
    source = ticks()
    result = run(source)
    verify(result, source, 1, result.costs, strategy_ref="tick-fixture@1.0.0")
    assert result.final_position == 0 and result.net_pnl == Decimal("-0.32")
    with localcontext() as context:
        context.prec = 6
        verify(result, source, 1, result.costs, strategy_ref="tick-fixture@1.0.0")


@pytest.mark.parametrize(
    "change",
    [
        {"price": Decimal("1.21001")},
        {"fee": Decimal("0")},
        {"quantity": Decimal("0.5")},
        {"quantity": Decimal("2")},
        {"side": "SELL"},
        {"observed_at": ticks().ticks[0].observed_at},
        {"price": Decimal("NaN")},
    ],
    ids=[
        "wrong-price",
        "wrong-fee",
        "partial",
        "outside-target",
        "wrong-side",
        "first-tick",
        "nan",
    ],
)
def test_changed_native_fill_is_refused(change):
    verify = verifier()
    result = run(ticks())
    damaged = replace(result, fills=(replace(result.fills[0], **change), *result.fills[1:]))
    with pytest.raises(ValueError):
        verify(damaged, ticks(), 1, result.costs, strategy_ref=result.strategy_ref)


@pytest.mark.parametrize(
    "change",
    [
        {"net_pnl": Decimal("1")},
        {"final_position": Decimal("1")},
        {"native_order_count": True},
        {"pnl_currency": "JPY"},
        {"engine_version": "unknown"},
        {"dataset_fingerprint": "0" * 64},
        {"strategy_ref": "another@1.0.0"},
    ],
)
def test_changed_result_binding_or_cash_is_refused(change):
    verify = verifier()
    result = run(ticks())
    with pytest.raises(ValueError):
        verify(
            replace(result, **change), ticks(), 1, result.costs, strategy_ref=result.strategy_ref
        )


def test_repeated_event_or_missing_source_quote_is_refused():
    verify = verifier()
    source = ticks()
    result = run(source)
    repeated = replace(result, fills=(result.fills[0], *result.fills))
    with pytest.raises(ValueError):
        verify(repeated, source, 1, result.costs, strategy_ref=result.strategy_ref)
    shortened = TickDataset((source.ticks[0], *source.ticks[2:]))
    with pytest.raises(ValueError):
        verify(result, shortened, 1, result.costs, strategy_ref=result.strategy_ref)


def test_native_runner_refuses_an_engine_fill_with_a_non_source_price(monkeypatch):
    import ai_quant_os.nautilus_runtime as runtime

    original = runtime._ReplayStrategy.on_order_filled

    def altered(self, event):
        original(self, event)
        self.fills[-1] = replace(self.fills[-1], price=self.fills[-1].price + Decimal(".00001"))

    monkeypatch.setattr(runtime._ReplayStrategy, "on_order_filled", altered)
    with pytest.raises(ValueError, match="source|fill"):
        run(ticks())


def test_duplicate_source_rows_are_refused_before_engine_construction(monkeypatch):
    import ai_quant_os.nautilus_runtime as runtime

    engines = []
    original = runtime.BacktestEngine

    def observed(*args, **kwargs):
        engines.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "BacktestEngine", observed)
    with pytest.raises(ValueError, match="duplicate"):
        run(TickDataset((*ticks().ticks, ticks().ticks[0])))
    assert engines == []


def test_cost_or_quantity_policy_cannot_be_reinterpreted():
    verify = verifier()
    result = run(ticks())
    for quantity, costs in (
        (True, result.costs),
        (2, result.costs),
        (1, BacktestCosts(Decimal(1), Decimal(".1"))),
    ):
        with pytest.raises(ValueError):
            verify(result, ticks(), quantity, costs, strategy_ref=result.strategy_ref)


@pytest.mark.parametrize("fee", [Decimal(".001")])
def test_impossible_fixed_fee_is_refused_without_native_execution(fee):
    verifier()
    from ai_quant_os.native_tick_evidence import canonical_tick_source

    with pytest.raises(ValueError):
        canonical_tick_source(ticks(), 1, BacktestCosts(Decimal(0), fee))


def test_strategy_cannot_replace_the_captured_dataset_during_engine_execution():
    from test_nautilus_ticks import Flip

    source = ticks()
    original = TickDataset(source.ticks)

    class MutatingFixture(Flip):
        def target_position(self, history):
            source.ticks = ()
            return super().target_position(history)

    result = run(source, MutatingFixture())
    assert result.dataset_fingerprint == original.fingerprint
    verifier()(result, original, 1, result.costs, strategy_ref=result.strategy_ref)


def test_jpy_fee_precision_and_source_currency_are_retained():
    from ai_quant_os.nautilus_runtime import run_nautilus_tick_backtest
    from test_nautilus_ticks import Flip

    source = TickDataset(replace(row, instrument_id="USDJPY") for row in ticks().ticks)
    costs = BacktestCosts(Decimal(0), Decimal(1))
    result = run_nautilus_tick_backtest(source, Flip(), costs, strategy_ref="tick-fixture@1.0.0")
    assert result.pnl_currency == "JPY" and all(fill.fee == 1 for fill in result.fills)
    verifier()(result, source, 1, costs, strategy_ref=result.strategy_ref)
    from ai_quant_os.native_tick_evidence import canonical_tick_source

    with pytest.raises(ValueError, match="currency precision"):
        canonical_tick_source(source, 1, BacktestCosts(Decimal(0), Decimal(".1")))
