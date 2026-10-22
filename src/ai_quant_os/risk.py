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


# V3.2 section 28 global safety ladder, lowest to highest severity:
# NORMAL -> CAUTION -> REDUCED -> NO_NEW_RISK -> FLATTEN_ONLY -> HALT
# Wire values match the names the Mission Control operator console already
# expects (REDUCED_RISK for the REDUCED rung). Kernel semantics: CAUTION
# still permits new risk (warning rung); REDUCED and NO_NEW_RISK refuse new
# risk; FLATTEN_ONLY permits only non-reversing reductions; HALT refuses
# everything. NOTE: keep this class docstring-free - a docstring leaks into
# published JSON schemas as a "description" and breaks schema parity tests.
class SafetyState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    REDUCED = "REDUCED_RISK"
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
        self._decision_safety_states: dict[str, SafetyState] = {}
        self._consumed: set[str] = set()
        self._lock = RLock()
        self._currency_policy = currency_policy
        self._currency_reservations: dict[str, CurrencyReservation] = {}
        self._decision_markets: dict[str, MarketSnapshot] = {}

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

    #: States from which the gated paper recovery workflow may return the
    #: kernel to NORMAL. HALT and FLATTEN_ONLY stay excluded: they require
    #: their own operator workflows, never this shortcut.
    RECOVERABLE_STATES = (SafetyState.CAUTION, SafetyState.REDUCED, SafetyState.NO_NEW_RISK)

    def _clear_recovery_custody(self) -> None:
        """Drop every pre-freeze authorization record (caller holds the lock)."""
        self._issued.clear()
        self._decision_safety_states.clear()
        self._consumed.clear()
        self._currency_reservations.clear()
        self._decision_markets.clear()

    def recover_paper_safety(self, *, before_change: Callable[[], None]) -> None:
        with self._lock:
            if self._safety_state not in self.RECOVERABLE_STATES:
                raise ValueError(
                    "Only CAUTION, REDUCED or NO_NEW_RISK paper state can be recovered"
                )
            before_change()
            self._clear_recovery_custody()
            self._safety_state = SafetyState.NORMAL

    def stage_paper_recovery_canary(self, *, before_change: Callable[[], None]) -> None:
        """Staged recovery landing (V3.2 section 13): land at REDUCED_RISK.

        Same gated preconditions and custody clearing as
        :meth:`recover_paper_safety`, but the kernel stops at the REDUCED
        rung as a canary stage instead of returning straight to NORMAL.
        NORMAL is reachable from the canary only through
        :meth:`promote_paper_canary`, whose caller must first obtain a
        fresh Recovery Verifier quorum over the landed canary.
        """
        with self._lock:
            if self._safety_state not in self.RECOVERABLE_STATES:
                raise ValueError(
                    "Only CAUTION, REDUCED or NO_NEW_RISK paper state can enter recovery canary"
                )
            before_change()
            self._clear_recovery_custody()
            self._safety_state = SafetyState.REDUCED

    def promote_paper_canary(self, *, before_change: Callable[[], None]) -> None:
        """Promote a canary-staged kernel (REDUCED) back to NORMAL.

        Only the REDUCED canary rung may be promoted here; every other
        state is refused, so this can never become a general recovery
        shortcut. ``before_change`` persists the promotion (re-validating
        the canary record) before the state flips.
        """
        with self._lock:
            if self._safety_state is not SafetyState.REDUCED:
                raise ValueError("Only a REDUCED recovery canary can be promoted to NORMAL")
            before_change()
            self._safety_state = SafetyState.NORMAL

    @contextmanager
    def authorization(self, intent: TradeIntent, decision: RiskDecision) -> Iterator[bool]:
        with self._lock:
            if self._issued.get(decision.risk_id) != (intent, decision):
                raise ValueError("Risk decision was not issued for this intent")
            yield (
                decision.decision == "APPROVE"
                and self._decision_safety_states.get(decision.risk_id) == self._safety_state
                and self._safety_state
                in (SafetyState.NORMAL, SafetyState.CAUTION, SafetyState.FLATTEN_ONLY)
                and decision.risk_id not in self._consumed
            )

    def consume(self, risk_id: str) -> None:
        with self._lock:
            if risk_id not in self._issued or risk_id in self._consumed:
                raise ValueError("Risk decision cannot be consumed")
            self._consumed.add(risk_id)
            self._decision_safety_states.pop(risk_id, None)
            self._decision_markets.pop(risk_id, None)

    def decision_market(self, decision: RiskDecision) -> MarketSnapshot:
        with self._lock:
            issued = self._issued.get(decision.risk_id)
            if (
                issued is None
                or issued[1] != decision
                or decision.decision != "APPROVE"
                or decision.risk_id in self._consumed
                or decision.risk_id not in self._decision_markets
            ):
                raise ValueError("Decision-price benchmark requires an unconsumed kernel approval")
            return self._decision_markets[decision.risk_id]

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
            decision_safety_state = self._safety_state
            if decision_safety_state == SafetyState.FLATTEN_ONLY:
                position = observed_portfolio.filled_net_quantity
                is_non_reversing_reduction = (
                    observed_portfolio.instrument_id == intent.instrument_id
                    and observed_portfolio.pending_buy_quantity == 0
                    and observed_portfolio.pending_sell_quantity == 0
                    and position != 0
                    and (
                        (position > 0 and intent.side == "SELL")
                        or (position < 0 and intent.side == "BUY")
                    )
                    and intent.desired_exposure <= abs(position)
                )
                if not is_non_reversing_reduction:
                    violations.append("FLATTEN_ONLY_REDUCTION_REQUIRED")
            elif decision_safety_state not in (SafetyState.NORMAL, SafetyState.CAUTION):
                violations.append(decision_safety_state.value)
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
            self._decision_safety_states[decision.risk_id] = decision_safety_state
            if outcome == "APPROVE":
                self._decision_markets[decision.risk_id] = market
            if (
                outcome == "APPROVE"
                and self._currency_policy is not None
                and currency_context is not None
            ):
                self._currency_reservations[decision.risk_id] = CurrencyReservation(
                    self._currency_policy, currency_context
                )
            return decision
