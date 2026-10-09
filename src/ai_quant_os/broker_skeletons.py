"""Explicit unsupported native execution interfaces; no SDK imports or fabricated facts."""

from dataclasses import dataclass
from typing import Literal

from ai_quant_os.broker import BrokerAck, BrokerUnavailable
from ai_quant_os.contracts import OrderCommand
from ai_quant_os.portfolio import PaperFill


@dataclass(frozen=True)
class DisabledBrokerSkeleton:
    broker_id: Literal["EXNESS", "IBKR", "ZERODHA"]

    def __post_init__(self):
        if self.broker_id not in {"EXNESS", "IBKR", "ZERODHA"}:
            raise ValueError("Unknown skeleton broker identity")

    @property
    def enabled(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    @property
    def symbol_map(self) -> dict[str, str]:
        return {}

    def place(self, command: OrderCommand) -> BrokerAck:
        raise BrokerUnavailable("Adapter is not implemented")

    def lookup(self, command: OrderCommand) -> BrokerAck | None:
        raise BrokerUnavailable("Adapter is not implemented")

    def fills(self, command: OrderCommand) -> tuple[PaperFill, ...]:
        raise BrokerUnavailable("Adapter is not implemented")

    def all_fills(self) -> tuple[PaperFill, ...]:
        raise BrokerUnavailable("Adapter is not implemented")


class IBKRAdapterSkeleton(DisabledBrokerSkeleton):
    def __init__(self):
        super().__init__("IBKR")


class ZerodhaAdapterSkeleton(DisabledBrokerSkeleton):
    def __init__(self):
        super().__init__("ZERODHA")


class ExnessExecutionSkeleton(DisabledBrokerSkeleton):
    def __init__(self):
        super().__init__("EXNESS")
