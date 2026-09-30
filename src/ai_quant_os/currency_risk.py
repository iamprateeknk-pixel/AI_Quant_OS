from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ai_quant_os.currency_portfolio import (
    CurrencyInstrument,
    CurrencyPendingOrder,
    FxRate,
    summarize_currency_portfolio,
)
from ai_quant_os.portfolio import PaperFill


@dataclass(frozen=True)
class CurrencyRiskContext:
    fills: tuple[PaperFill, ...]
    pending: tuple[CurrencyPendingOrder, ...]
    instruments: tuple[CurrencyInstrument, ...]
    rates: tuple[FxRate, ...]


@dataclass(frozen=True)
class CurrencyRiskPolicy:
    reporting_currency: str
    max_currency_exposure_reporting: Decimal
    max_gross_exposure_reporting: Decimal
    max_rate_age_seconds: int
    instruments: tuple[CurrencyInstrument, ...] = (CurrencyInstrument("EURUSD", "EUR", "USD"),)

    def __post_init__(self) -> None:
        if (
            len(self.reporting_currency) != 3
            or not self.reporting_currency.isascii()
            or not self.reporting_currency.isalpha()
            or not self.reporting_currency.isupper()
        ):
            raise ValueError("Invalid reporting currency")
        if (
            any(
                not value.is_finite() or value <= 0
                for value in (
                    self.max_currency_exposure_reporting,
                    self.max_gross_exposure_reporting,
                )
            )
            or self.max_rate_age_seconds <= 0
        ):
            raise ValueError("Currency risk limits must be positive and finite")
        if not self.instruments or len({item.instrument_id for item in self.instruments}) != len(
            self.instruments
        ):
            raise ValueError("Currency risk instrument metadata must be unique and nonempty")

    def violations(
        self, context: CurrencyRiskContext, candidate: CurrencyPendingOrder, *, now: datetime
    ) -> tuple[str, ...]:
        if context.instruments != self.instruments:
            return ("CURRENCY_INSTRUMENT_POLICY_MISMATCH",)
        snapshots = tuple(
            summarize_currency_portfolio(
                context.fills,
                pending=pending,
                instruments=self.instruments,
                rates=context.rates,
                reporting_currency=self.reporting_currency,
                now=now,
                max_rate_age_seconds=self.max_rate_age_seconds,
            )
            for pending in (context.pending, context.pending + (candidate,))
        )
        before, after = snapshots
        issues = tuple(sorted(set(before.issues + after.issues)))
        if issues:
            return ("CURRENCY_VALUATION_UNAVAILABLE",) + issues
        prior = {item.currency: item.worst_case_exposure_reporting for item in before.currencies}
        violations: list[str] = []
        if any(
            item.worst_case_exposure_reporting > self.max_currency_exposure_reporting
            and item.worst_case_exposure_reporting > prior.get(item.currency, Decimal(0))
            for item in after.currencies
        ):
            violations.append("CURRENCY_EXPOSURE_LIMIT")
        if (
            after.gross_worst_case_exposure_reporting > self.max_gross_exposure_reporting
            and after.gross_worst_case_exposure_reporting
            > before.gross_worst_case_exposure_reporting
        ):
            violations.append("GROSS_CURRENCY_EXPOSURE_LIMIT")
        return tuple(violations)


@dataclass(frozen=True)
class CurrencyReservation:
    policy: CurrencyRiskPolicy
    context: CurrencyRiskContext

    def validate(
        self,
        fills: tuple[PaperFill, ...],
        pending: tuple[CurrencyPendingOrder, ...],
        candidate: CurrencyPendingOrder,
        *,
        now: datetime,
    ) -> None:
        if sorted(fills, key=lambda item: item.fill_id) != sorted(
            self.context.fills, key=lambda item: item.fill_id
        ) or sorted(pending, key=lambda item: item.order_id) != sorted(
            self.context.pending, key=lambda item: item.order_id
        ):
            raise ValueError("Currency ledger changed; re-evaluate risk")
        violations = self.policy.violations(self.context, candidate, now=now)
        if violations:
            raise ValueError("Currency reservation rejected: " + ",".join(violations))
