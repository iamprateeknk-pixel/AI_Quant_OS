"""Isolated native replays without platform broker or candidate lifecycle writes."""

from decimal import Context, Inexact, localcontext

from ai_quant_os.counterfactual_results import CounterfactualReport, ReplayRun
from ai_quant_os.market_data import BarDataset
from ai_quant_os.registered_backtest import run_registered_backtest
from ai_quant_os.strategy_registry import prepare_builtin


def run_counterfactual(inputs, strategies, store):
    token, cached = store.reserve(inputs)
    if cached is not None:
        return cached
    context = Context(prec=28)
    context.traps[Inexact] = True
    with localcontext(context):
        results, reason = [], None
        stage = "SOURCE_VERIFICATION_FAILURE"
        try:
            parent = inputs.candidate.report.holdout
            for variant in inputs.variants:
                _, binding = prepare_builtin(variant.genome, strategies)
                if (
                    binding.genome != variant.genome
                    or binding.implementation != parent.binding.implementation
                ):
                    raise ValueError("Frozen implementation mismatch")
            dataset = BarDataset(inputs.source_bars)
            stage = "NATIVE_EXECUTION_FAILURE"
            for variant in inputs.variants:
                run = run_registered_backtest(
                    dataset, variant.genome, variant.costs, strategies, quantity=variant.quantity
                )
                results.append(ReplayRun(variant, run, run.result.net_pnl - parent.result.net_pnl))
                if variant.name == "BASELINE" and run != parent:
                    reason = "BASELINE_REPRODUCIBILITY_FAILURE"
                    break
        except (ValueError, RuntimeError, OSError, ArithmeticError):
            reason = stage
        report = CounterfactualReport(
            inputs, "COMPLETED" if reason is None else "FAILED", tuple(results), reason
        )
        store.finish(token, report)
        return report
