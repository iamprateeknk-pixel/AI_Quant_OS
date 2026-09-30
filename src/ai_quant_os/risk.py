from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Callable, Iterator
from uuid import uuid4

from ai_quant_os.contracts import RiskDecision, TradeIntent, _require_aware
from ai_quant_os.market import MarketSnapshot
from ai_quant_os.portfolio import PortfolioExposure
from ai_quant_os.currency_portfolio import CurrencyPendingOrder
from ai_quant_os.currency_risk import CurrencyReservation, CurrencyRiskContext, CurrencyRiskPolicy


class SafetyState(str, Enum):
    NORMAL = "NORMAL"
    NO_NEW_RISK = "NO_NEW_RISK"
    FLATTEN_ONLY = "FLATTEN_ONLY"
    HALT = "HALT"


@dataclass(frozen=True)
class RiskLimits:
    max_order_exposure: Decimal
    max_portfolio_exposure: Decimal
    max_quote_age_seconds: int

    def __post_init__(self) -> None:
        if (
            not self.max_order_exposure.is_finite()
            or self.max_order_exposure <= 0
            or not self.max_portfolio_exposure.is_finite()
            or self.max_portfolio_exposure <= 0
            or self.max_quote_age_seconds <= 0
        ):
            raise ValueError("Risk limits must be positive")


class RiskKernel:
    def __init__(
        self, limits: RiskLimits, *, currency_policy: CurrencyRiskPolicy | None = None
    ) -> None:
        self._limits = limits
        self._safety_state = SafetyState.NORMAL
        self._issued: dict[str, tuple[TradeIntent, RiskDecision]] = {}
        self._consumed: set[str] = set()
        self._lock = RLock()
        self._currency_policy = currency_policy
        self._currency_reservations: dict[str, CurrencyReservation] = {}

    @property
    def safety_state(self) -> SafetyState:
        with self._lock:
            return self._safety_state

    @property
    def max_portfolio_exposure(self) -> Decimal:
        return self._limits.max_portfolio_exposure

    def set_safety_state(
        self, state: SafetyState, *, before_change: Callable[[], None] | None = None
    ) -> None:
        with self._lock:
            severity = list(SafetyState)
            if severity.index(state) < severity.index(self._safety_state):
                raise ValueError("Safety state recovery requires a separate gated workflow")
            if before_change is not None:
                before_change()
            self._safety_state = state

    def recover_paper_safety(self, *, before_change: Callable[[], None]) -> None:
        with self._lock:
            if self._safety_state != SafetyState.NO_NEW_RISK:
                raise ValueError("Only NO_NEW_RISK paper state can be recovered")
            before_change()
            self._issued.clear()
            self._consumed.clear()
            self._currency_reservations.clear()
            self._safety_state = SafetyState.NORMAL

    @contextmanager
    def authorization(self, intent: TradeIntent, decision: RiskDecision) -> Iterator[bool]:
        with self._lock:
            if self._issued.get(decision.risk_id) != (intent, decision):
                raise ValueError("Risk decision was not issued for this intent")
            yield (
                decision.decision == "APPROVE"
                and self._safety_state == SafetyState.NORMAL
                and decision.risk_id not in self._consumed
            )

    def consume(self, risk_id: str) -> None:
        with self._lock:
            if risk_id not in self._issued or risk_id in self._consumed:
                raise ValueError("Risk decision cannot be consumed")
            self._consumed.add(risk_id)

    def currency_reservation(self, decision: RiskDecision) -> CurrencyReservation | None:
        with self._lock:
            if self._currency_policy is None:
                return None
            if decision.risk_id not in self._currency_reservations:
                raise ValueError("Currency authorization context is required")
            return self._currency_reservations[decision.risk_id]

    def evaluate(
        self,
        intent: TradeIntent,
        market: MarketSnapshot,
        *,
        now: datetime,
        portfolio: PortfolioExposure | None = None,
        currency_context: CurrencyRiskContext | None = None,
    ) -> RiskDecision:
        _require_aware(now)
        observed_portfolio = portfolio or PortfolioExposure.empty(intent.instrument_id)
        violations: list[str] = []
        rules = (
            "SAFETY_STATE",
            "INTENT_EXPIRY",
            "MARKET_TRUST",
            "MARKET_FRESHNESS",
            "WORLD_STATE_VERSION",
            "INSTRUMENT",
            "SPREAD",
            "ORDER_EXPOSURE_LIMIT",
            "PORTFOLIO_EXPOSURE_LIMIT",
        )
        if self._safety_state != SafetyState.NORMAL:
            violations.append(self._safety_state.value)
        if now >= intent.valid_until or now < intent.created_at:
            violations.append("INTENT_EXPIRED")
        if not market.trusted:
            violations.append("UNTRUSTED_MARKET")
        age = (now - market.observed_at).total_seconds()
        if age < 0 or age > self._limits.max_quote_age_seconds:
            violations.append("STALE_MARKET")
        if intent.world_state_version != market.world_state_version:
            violations.append("WORLD_STATE_MISMATCH")
        if intent.instrument_id != market.instrument_id:
            violations.append("INSTRUMENT_MISMATCH")
        if market.spread > intent.max_spread:
            violations.append("SPREAD_LIMIT")
        if intent.desired_exposure > self._limits.max_order_exposure:
            violations.append("ORDER_EXPOSURE_LIMIT")
        if self._currency_policy is not None:
            rules += (
                "CURRENCY_EXPOSURE_LIMIT",
                "GROSS_CURRENCY_EXPOSURE_LIMIT",
                "CURRENCY_PRICE_FRESHNESS",
            )
            if currency_context is None:
                violations.append("CURRENCY_CONTEXT_REQUIRED")
            else:
                instrument = next(
                    (
                        item
                        for item in self._currency_policy.instruments
                        if item.instrument_id == intent.instrument_id
                    ),
                    None,
                )
                if instrument is not None and not any(
                    (rate.base_currency, rate.quote_currency)
                    == (instrument.base_currency, instrument.quote_currency)
                    and (rate.bid, rate.ask, rate.observed_at, rate.trusted)
                    == (market.bid, market.ask, market.observed_at, market.trusted)
                    for rate in currency_context.rates
                ):
                    violations.append("CURRENCY_MARKET_MISMATCH")
                try:
                    violations.extend(
                        self._currency_policy.violations(
                            currency_context,
                            CurrencyPendingOrder(
                                f"proposal:{intent.intent_id}",
                                intent.instrument_id,
                                intent.side,
                                intent.desired_exposure,
                                intent.max_slippage,
                            ),
                            now=now,
                        )
                    )
                except ValueError:
                    violations.append("INVALID_CURRENCY_CONTEXT")
        if intent.instrument_id != observed_portfolio.instrument_id:
            violations.append("PORTFOLIO_INSTRUMENT_MISMATCH")
        else:
            resulting_exposure = observed_portfolio.with_order(intent.side, intent.desired_exposure)
            if (
                resulting_exposure > self._limits.max_portfolio_exposure
                and resulting_exposure > observed_portfolio.worst_case_quantity
            ):
                violations.append("PORTFOLIO_EXPOSURE_LIMIT")
        with self._lock:
            if (
                self._safety_state != SafetyState.NORMAL
                and self._safety_state.value not in violations
            ):
                violations.append(self._safety_state.value)
            outcome = (
                "HALT"
                if self._safety_state == SafetyState.HALT
                else "REJECT"
                if violations
                else "APPROVE"
            )
            decision = RiskDecision(
                risk_id=str(uuid4()),
                intent_id=intent.intent_id,
                decision=outcome,
                approved_exposure=intent.desired_exposure if outcome == "APPROVE" else Decimal(0),
                rules_evaluated=rules,
                violations=tuple(violations),
                portfolio_snapshot_id=observed_portfolio.snapshot_id,
                created_at=now,
            )
            self._issued[decision.risk_id] = (intent, decision)
            if (
                outcome == "APPROVE"
                and self._currency_policy is not None
                and currency_context is not None
            ):
                self._currency_reservations[decision.risk_id] = CurrencyReservation(
                    self._currency_policy, currency_context
                )
            return decision
