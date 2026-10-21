"""Versioned paper risk policy (V3.2 sections 28 and 34).

Before this module the paper ceilings lived as literals inside ``api.py``
(1,000 base units per order, 1,500 portfolio, USD 2,000 per currency and
USD 4,000 gross). Those values are now a named, versioned policy object:

* the defaults are byte-for-byte the old literals, so behaviour is
  unchanged when no policy file is configured;
* an operator may point ``AI_QUANT_RISK_POLICY_PATH`` at a JSON file with
  ``policy_version``, the limit fields below and no unknown keys;
* every loaded policy carries a SHA-256 digest of its canonical form so
  audit records can name the exact policy that produced a decision;
* invalid, unknown-field or non-positive policies fail closed at startup.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from ai_quant_os.currency_portfolio import CurrencyInstrument
from ai_quant_os.currency_risk import CurrencyRiskPolicy
from ai_quant_os.risk import RiskLimits

POLICY_FORMAT_VERSION = "1.0"

_FIELDS = {
    "policy_version",
    "max_order_exposure",
    "max_portfolio_exposure",
    "max_quote_age_seconds",
    "reporting_currency",
    "max_currency_exposure_reporting",
    "max_gross_exposure_reporting",
    "max_rate_age_seconds",
}


@dataclass(frozen=True)
class PaperRiskPolicy:
    policy_version: str = "paper-risk-1.0.0"
    max_order_exposure: Decimal = Decimal("1000")
    max_portfolio_exposure: Decimal = Decimal("1500")
    max_quote_age_seconds: int = 5
    reporting_currency: str = "USD"
    max_currency_exposure_reporting: Decimal = Decimal("2000")
    max_gross_exposure_reporting: Decimal = Decimal("4000")
    max_rate_age_seconds: int = 5

    def __post_init__(self) -> None:
        if not self.policy_version or len(self.policy_version) > 64:
            raise ValueError("Risk policy version is required")
        for value in (
            self.max_order_exposure,
            self.max_portfolio_exposure,
            self.max_currency_exposure_reporting,
            self.max_gross_exposure_reporting,
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError("Risk policy limits must be positive and finite")
        if self.max_quote_age_seconds <= 0 or self.max_rate_age_seconds <= 0:
            raise ValueError("Risk policy age limits must be positive")
        if len(self.reporting_currency) != 3 or not self.reporting_currency.isupper():
            raise ValueError("Risk policy reporting currency must be a 3-letter code")

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {key: str(value) for key, value in sorted(asdict(self).items())},
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()

    def risk_limits(self) -> RiskLimits:
        return RiskLimits(
            max_order_exposure=self.max_order_exposure,
            max_portfolio_exposure=self.max_portfolio_exposure,
            max_quote_age_seconds=self.max_quote_age_seconds,
        )

    def currency_policy(self, instruments: tuple[CurrencyInstrument, ...]) -> CurrencyRiskPolicy:
        return CurrencyRiskPolicy(
            self.reporting_currency,
            self.max_currency_exposure_reporting,
            self.max_gross_exposure_reporting,
            self.max_rate_age_seconds,
            instruments,
        )


DEFAULT_POLICY = PaperRiskPolicy()


def _decimal(raw: object, field: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Risk policy field {field} is not a decimal") from error
    return value


def load_risk_policy(path: Path | None = None) -> PaperRiskPolicy:
    """Load the configured policy, or the built-in default.

    ``path`` wins; otherwise ``AI_QUANT_RISK_POLICY_PATH`` is honoured.
    A configured-but-broken file is a startup error, never a silent
    fallback to the default (fail closed).
    """
    configured = path or (
        Path(os.environ["AI_QUANT_RISK_POLICY_PATH"])
        if os.getenv("AI_QUANT_RISK_POLICY_PATH")
        else None
    )
    if configured is None:
        return DEFAULT_POLICY
    raw = json.loads(configured.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format_version") != POLICY_FORMAT_VERSION:
        raise ValueError("Risk policy file has an unsupported format version")
    fields = set(raw) - {"format_version"}
    if fields != _FIELDS:
        raise ValueError(
            f"Risk policy fields mismatch; missing={sorted(_FIELDS - fields)}, "
            f"unknown={sorted(fields - _FIELDS)}"
        )
    return PaperRiskPolicy(
        policy_version=str(raw["policy_version"]),
        max_order_exposure=_decimal(raw["max_order_exposure"], "max_order_exposure"),
        max_portfolio_exposure=_decimal(raw["max_portfolio_exposure"], "max_portfolio_exposure"),
        max_quote_age_seconds=int(raw["max_quote_age_seconds"]),
        reporting_currency=str(raw["reporting_currency"]),
        max_currency_exposure_reporting=_decimal(
            raw["max_currency_exposure_reporting"], "max_currency_exposure_reporting"
        ),
        max_gross_exposure_reporting=_decimal(
            raw["max_gross_exposure_reporting"], "max_gross_exposure_reporting"
        ),
        max_rate_age_seconds=int(raw["max_rate_age_seconds"]),
    )
