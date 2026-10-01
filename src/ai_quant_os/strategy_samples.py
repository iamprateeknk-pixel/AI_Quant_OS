"""Deterministic demonstration plugins; no model calls or inferred trading advantage."""

from dataclasses import dataclass
from types import MappingProxyType

from ai_quant_os.market_data import Bar


@dataclass(frozen=True)
class BuyAndHold:
    def target_position(self, history: tuple[Bar, ...]) -> int:
        return int(bool(history))


@dataclass(frozen=True)
class MovingAverageCrossover:
    fast: int = 2
    slow: int = 3

    def __post_init__(self):
        if (
            type(self.fast) is not int
            or type(self.slow) is not int
            or not 1 <= self.fast < self.slow <= 4096
        ):
            raise ValueError("Invalid moving average windows")

    def target_position(self, history: tuple[Bar, ...]) -> int:
        if len(history) < self.slow:
            return 0
        fast = sum(bar.close for bar in history[-self.fast :]) / self.fast
        slow = sum(bar.close for bar in history[-self.slow :]) / self.slow
        return int(fast > slow)


SAMPLE_STRATEGIES = MappingProxyType(
    {"buy-and-hold": BuyAndHold, "moving-average-cross": MovingAverageCrossover}
)
