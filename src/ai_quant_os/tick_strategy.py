"""Pure recorded-quote plugin contract and a trusted interface demonstration."""

from dataclasses import dataclass
from typing import Protocol

from ai_quant_os.tick_data import Tick


class TickStrategy(Protocol):
    def target_position(self, history: tuple[Tick, ...]) -> int: ...


@dataclass(frozen=True)
class TickMomentum:
    def target_position(self, history: tuple[Tick, ...]) -> int:
        if len(history) < 2:
            return 0
        previous, latest = history[-2:]
        change = latest.bid + latest.ask - previous.bid - previous.ask
        return 1 if change > 0 else -1 if change < 0 else 0
