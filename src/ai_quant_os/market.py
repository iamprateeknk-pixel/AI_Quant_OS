from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ai_quant_os.contracts import _require_aware


@dataclass(frozen=True)
class MarketSnapshot:
    instrument_id: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    world_state_version: str
    trusted: bool

    def __post_init__(self) -> None:
        _require_aware(self.observed_at)
        if not self.instrument_id or not self.world_state_version:
            raise ValueError("Market identifiers are required")
        if (
            not self.bid.is_finite()
            or not self.ask.is_finite()
            or self.bid <= 0
            or self.ask < self.bid
        ):
            raise ValueError("Invalid market price")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid
