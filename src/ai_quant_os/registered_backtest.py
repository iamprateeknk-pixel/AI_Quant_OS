"""Provenance-bound native execution for trusted allowlisted strategy genomes."""

from dataclasses import dataclass

from ai_quant_os.backtest import BacktestCosts
from ai_quant_os.market_data import BarDataset
from ai_quant_os.nautilus_runtime import (
    NautilusResult,
    run_nautilus_backtest,
    run_nautilus_tick_backtest,
)
from ai_quant_os.strategy_contracts import StrategyBinding, StrategyGenome
from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin, verify_implementation
from ai_quant_os.tick_data import TickDataset


@dataclass(frozen=True)
class RegisteredBacktest:
    result: NautilusResult
    binding: StrategyBinding


def run_registered_backtest(
    dataset,
    genome: StrategyGenome,
    costs: BacktestCosts,
    registry: StrategyRegistry,
    *,
    quantity: int = 1,
) -> RegisteredBacktest:
    expected = BarDataset if genome.input_kind == "BAR" else TickDataset
    if not isinstance(dataset, expected):
        raise ValueError("Strategy input does not match genome")
    plugin, binding = prepare_builtin(genome, registry)
    runner = run_nautilus_backtest if genome.input_kind == "BAR" else run_nautilus_tick_backtest
    result = runner(dataset, plugin, costs, strategy_ref=genome.strategy_ref, quantity=quantity)
    verify_implementation(binding.implementation)
    if registry.get(genome.strategy_ref) != binding.implementation:
        raise ValueError("Registered implementation changed during execution")
    return RegisteredBacktest(result, binding)
