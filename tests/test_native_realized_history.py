from decimal import Decimal

import pytest

from test_nautilus_ticks import run, ticks


def test_closed_and_reopened_tick_positions_retain_all_realized_pnl():
    result = run(ticks())
    assert result.final_position == 0
    assert result.native_realized_pnl == Decimal("-.32")
    assert result.native_realized_pnl == result.net_pnl


def test_closed_history_and_current_open_commission_are_both_retained():
    from ai_quant_os.tick_data import TickDataset

    result = run(TickDataset(ticks().ticks[:3]))
    assert result.final_position == -1
    # Long closes at 1.30 after opening at 1.21: .09 minus .10 and .05 fees.
    # The new short contributes its .05 entry fee before any later close.
    assert result.native_realized_pnl == Decimal("-.11")


def test_repeated_close_reopen_cycles_conserve_native_account_balance(monkeypatch):
    import ai_quant_os.nautilus_runtime as runtime
    from ai_quant_os.tick_data import TickDataset
    from dataclasses import replace
    from datetime import timedelta

    original = runtime.BacktestEngine
    balances = []

    class ObservedEngine:
        def __init__(self, *args, **kwargs):
            self.engine = original(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.engine, name)

        def dispose(self):
            from nautilus_trader.model.objects import Currency

            balances.append(
                self.engine.cache.accounts()[0].balance_total(Currency.from_str("USD")).as_decimal()
            )
            self.engine.dispose()

    monkeypatch.setattr(runtime, "BacktestEngine", ObservedEngine)
    source = ticks().ticks
    source = TickDataset(
        (
            *source,
            *(
                replace(
                    source[i],
                    sequence=i + 4,
                    observed_at=source[i].observed_at + timedelta(seconds=4),
                )
                for i in range(4)
            ),
        )
    )

    class Alternating:
        def target_position(self, history):
            return (1, 0, -1, 0)[(len(history) - 1) % 4]

    result = run(source, Alternating())
    assert result.native_realized_pnl == balances[0] - Decimal(100000)
    assert result.native_realized_pnl != 0


def test_no_closed_positions_does_not_double_count_open_fees():
    class Long:
        def target_position(self, history):
            return 1

    result = run(ticks(), Long())
    assert result.final_position == 1 and result.native_realized_pnl == Decimal("-.10")


def test_missing_closed_event_cannot_return_an_account_inconsistent_result(monkeypatch):
    import ai_quant_os.nautilus_runtime as runtime

    monkeypatch.setattr(runtime._ReplayStrategy, "on_position_closed", lambda self, event: None)
    with pytest.raises(ValueError, match="closed|realized|account"):
        run(ticks())


@pytest.mark.parametrize("fault", ["duplicate", "amount"])
def test_duplicate_or_altered_closed_observation_is_refused(monkeypatch, fault):
    import ai_quant_os.nautilus_runtime as runtime
    from nautilus_trader.model.objects import Money
    from types import SimpleNamespace

    original = runtime._ReplayStrategy.on_position_closed

    def observed(self, event):
        if fault == "duplicate":
            original(self, event)
            original(self, event)
        else:
            original(
                self,
                SimpleNamespace(
                    instrument_id=event.instrument_id,
                    ts_closed=event.ts_closed,
                    realized_pnl=Money(
                        event.realized_pnl.as_decimal() + Decimal(1), event.realized_pnl.currency
                    ),
                ),
            )

    monkeypatch.setattr(runtime._ReplayStrategy, "on_position_closed", observed)
    with pytest.raises(ValueError, match="closed|realized"):
        run(ticks())


def test_legacy_realized_observation_cannot_be_silently_adopted_for_counterfactual(tmp_path):
    from test_research_candidates import archived_workspace
    from ai_quant_os.research_candidates import build_candidate_bundle
    from ai_quant_os.counterfactual_contracts import CounterfactualPlan, ReplayVariant
    from ai_quant_os.counterfactual_results import ReplayInputs
    from ai_quant_os.counterfactual_pipeline import run_counterfactual
    from ai_quant_os.counterfactual_store import CounterfactualStore

    report, research, strategies = archived_workspace(tmp_path)
    candidate = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    plan = CounterfactualPlan(
        "legacy-realized-0131",
        candidate.manifest.candidate_id,
        candidate.manifest.report_sha256,
        report.plan.holdout_fingerprint,
        (ReplayVariant("half-size", report.selection.genome, 50, report.plan.costs),),
    )
    observed = run_counterfactual(
        ReplayInputs(plan, candidate, report.adversarial.source_bars),
        strategies,
        CounterfactualStore(tmp_path / "counterfactual.sqlite3"),
    )
    assert observed.outcome == "FAILED" and observed.reason == "BASELINE_REPRODUCIBILITY_FAILURE"
