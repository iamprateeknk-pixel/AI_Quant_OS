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

# Optional fields: accepted when present, defaulted when absent. The eight
# fields in ``_FIELDS`` remain required; anything else fails closed.
_OPTIONAL_FIELDS = {"twin_evidence_weight", "recovery_canary_min_soak_seconds"}


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
    twin_evidence_weight: Decimal = Decimal("0")
    #: Minimum soak for a staged recovery canary (V3.2 section 13): after a
    #: paper recovery lands at REDUCED_RISK, at least this many seconds must
    #: elapse -- with no health incident detected during the canary -- before
    #: the promotion step may return the deployment to NORMAL.
    recovery_canary_min_soak_seconds: int = 300

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
        weight = self.twin_evidence_weight
        if not isinstance(weight, Decimal) or not weight.is_finite() or weight < 0 or weight > 1:
            raise ValueError("Risk policy twin evidence weight must lie in [0, 1]")
        soak = self.recovery_canary_min_soak_seconds
        if type(soak) is not int or not 0 <= soak <= 7 * 24 * 3600:
            raise ValueError("Risk policy recovery canary soak must be an int in [0, 604800]")

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
    known = _FIELDS | _OPTIONAL_FIELDS
    if _FIELDS - fields or fields - known:
        raise ValueError(
            f"Risk policy fields mismatch; missing={sorted(_FIELDS - fields)}, "
            f"unknown={sorted(fields - known)}"
        )
    twin_weight = (
        _decimal(raw["twin_evidence_weight"], "twin_evidence_weight")
        if "twin_evidence_weight" in raw
        else Decimal("0")
    )
    if "recovery_canary_min_soak_seconds" in raw:
        soak_raw = raw["recovery_canary_min_soak_seconds"]
        if type(soak_raw) is not int:
            raise ValueError("Risk policy field recovery_canary_min_soak_seconds is not an int")
        canary_soak = soak_raw
    else:
        canary_soak = 300
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
        twin_evidence_weight=twin_weight,
        recovery_canary_min_soak_seconds=canary_soak,
    )
