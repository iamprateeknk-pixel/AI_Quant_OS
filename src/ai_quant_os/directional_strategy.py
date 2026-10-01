"""Trusted directional demonstration plugin with explicit long/short/flat targets."""

from dataclasses import dataclass
from decimal import Context, Inexact, localcontext

from ai_quant_os.market_data import Bar
from ai_quant_os.strategy_samples import MovingAverageCrossover

DIRECTIONAL_REF = "moving-average-directional@1.0.0"


@dataclass(frozen=True)
class DirectionalMovingAverage:
    fast: int = 2
    slow: int = 3

    def __post_init__(self):
        MovingAverageCrossover(self.fast, self.slow)

    def target_position(self, history: tuple[Bar, ...]) -> int:
        if len(history) < self.slow:
            return 0
        context = Context(prec=28)
        context.traps[Inexact] = True
        with localcontext(context):
            # Cross multiplication compares averages without rounding division.
            fast = sum((bar.close for bar in history[-self.fast :])) * self.slow
            slow = sum((bar.close for bar in history[-self.slow :])) * self.fast
            return 1 if fast > slow else -1 if fast < slow else 0
