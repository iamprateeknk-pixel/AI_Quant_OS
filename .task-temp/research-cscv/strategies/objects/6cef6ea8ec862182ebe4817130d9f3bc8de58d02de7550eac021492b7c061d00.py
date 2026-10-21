from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


def _require_aware(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    desired_exposure: Decimal
    risk_budget: Decimal
    max_spread: Decimal
    max_slippage: Decimal
    valid_until: datetime
    world_state_version: str
    evidence_refs: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.created_at)
        _require_aware(self.valid_until)
        if not self.intent_id or not self.strategy_id or not self.instrument_id:
            raise ValueError("Intent identifiers are required")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("Unsupported side")
        if not self.desired_exposure.is_finite() or self.desired_exposure <= 0:
            raise ValueError("Exposure must be positive and finite")
        if not all(
            bound.is_finite() for bound in (self.risk_budget, self.max_spread, self.max_slippage)
        ):
            raise ValueError("Trading bounds must be finite")
        if self.risk_budget <= 0 or self.max_spread < 0 or self.max_slippage < 0:
            raise ValueError("Invalid trading bounds")
        if self.valid_until <= self.created_at:
            raise ValueError("Intent must expire after creation")


@dataclass(frozen=True)
class RiskDecision:
    risk_id: str
    intent_id: str
    decision: Literal["APPROVE", "RESIZE", "REJECT", "HALT"]
    approved_exposure: Decimal
    rules_evaluated: tuple[str, ...]
    violations: tuple[str, ...]
    portfolio_snapshot_id: str
    created_at: datetime


@dataclass(frozen=True)
class OrderCommand:
    order_id: str
    idempotency_key: str
    risk_id: str
    broker_route: str
    instrument_id: str
    order_type: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: str
    expires_at: datetime
