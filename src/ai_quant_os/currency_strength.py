"""Relative currency log returns from a checked spanning forest of FX observations."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware

RESIDUAL_LIMIT_BPS = Decimal("0.00000001")


def _currency(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None


@dataclass(frozen=True)
class PairHistory:
    instrument_id: str
    base_currency: str
    quote_currency: str
    observed_at: tuple[datetime, ...]
    closes: tuple[Decimal, ...]
    evidence_ref: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 128
            or not _currency(self.base_currency)
            or not _currency(self.quote_currency)
            or self.base_currency == self.quote_currency
            or not isinstance(self.evidence_ref, str)
            or not 1 <= len(self.evidence_ref) <= 256
            or not isinstance(self.closes, tuple)
            or not isinstance(self.observed_at, tuple)
            or not 3 <= len(self.closes) <= 64
            or len(self.closes) != len(self.observed_at)
        ):
            raise ValueError("Invalid currency pair history")
        for moment in self.observed_at:
            if not isinstance(moment, datetime):
                raise ValueError("Invalid pair observation time")
            _require_aware(moment)
        if any(left >= right for left, right in zip(self.observed_at, self.observed_at[1:])):
            raise ValueError("Pair observation times must strictly increase")
        if any(
            not isinstance(price, Decimal)
            or not price.is_finite()
            or not Decimal("1e-12") <= price <= Decimal("1e12")
            or len(price.as_tuple().digits) > 64
            for price in self.closes
        ):
            raise ValueError("Invalid bounded pair price")


@dataclass(frozen=True)
class EdgeContribution:
    instrument_id: str
    coefficient: Decimal
    return_bps: Decimal
    contribution_bps: Decimal


@dataclass(frozen=True)
class CurrencyEstimate:
    currency: str
    component: tuple[str, ...]
    status: Literal["AVAILABLE", "WITHHELD", "UNAVAILABLE"]
    score_bps: Decimal | None
    decomposition: tuple[EdgeContribution, ...]


@dataclass(frozen=True)
class PairResidual:
    instrument_id: str
    dependent: bool
    max_residual_bps: Decimal


@dataclass(frozen=True)
class CurrencyStrength:
    status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNALIGNED", "INCONSISTENT"]
    estimates: tuple[CurrencyEstimate, ...]
    basis_instruments: tuple[str, ...]
    dependent_instruments: tuple[str, ...]
    residuals: tuple[PairResidual, ...]
    window_start: datetime | None
    window_end: datetime | None
    observation_count: int


def _forest(currencies: tuple[str, ...], pairs: tuple[PairHistory, ...]):
    groups = {currency: currency for currency in currencies}
    basis = []
    for pair in pairs:
        base, quote = groups[pair.base_currency], groups[pair.quote_currency]
        if base != quote:
            basis.append(pair)
            groups = {key: base if value == quote else value for key, value in groups.items()}
    return tuple(basis), groups


def _coefficients(component: tuple[str, ...], basis: tuple[PairHistory, ...]):
    root = component[0]
    coefficients = {root: (Decimal(0),) * len(basis)}
    pending = [root]
    for current in pending:
        for index, pair in enumerate(basis):
            if current not in (pair.base_currency, pair.quote_currency):
                continue
            neighbor = pair.quote_currency if current == pair.base_currency else pair.base_currency
            if neighbor in coefficients:
                continue
            row = list(coefficients[current])
            row[index] += -1 if current == pair.base_currency else 1
            coefficients[neighbor] = tuple(row)
            pending.append(neighbor)
    averages = tuple(
        sum(coefficients[currency][index] for currency in component) / len(component)
        for index in range(len(basis))
    )
    return {
        currency: tuple(value - mean for value, mean in zip(row, averages))
        for currency, row in coefficients.items()
    }


def _unavailable(currencies: tuple[str, ...], status: str) -> CurrencyStrength:
    return CurrencyStrength(
        status,
        tuple(
            CurrencyEstimate(currency, (currency,), "UNAVAILABLE", None, ())
            for currency in currencies
        ),
        (),
        (),
        (),
        None,
        None,
        0,
    )


def currency_strength(
    currencies: tuple[str, ...], pairs: tuple[PairHistory, ...]
) -> CurrencyStrength:
    """Use each algebraic degree of freedom once; never infer statistical confidence."""
    if (
        not 1 <= len(currencies) <= 16
        or len(set(currencies)) != len(currencies)
        or not all(_currency(currency) for currency in currencies)
        or len(pairs) > 32
        or any(not isinstance(pair, PairHistory) for pair in pairs)
        or len({pair.instrument_id for pair in pairs}) != len(pairs)
        or any({pair.base_currency, pair.quote_currency} - set(currencies) for pair in pairs)
    ):
        raise ValueError("Invalid currency network")
    currencies = tuple(sorted(currencies))
    pairs = tuple(
        sorted(
            pairs,
            key=lambda pair: (
                min(pair.base_currency, pair.quote_currency),
                max(pair.base_currency, pair.quote_currency),
                pair.instrument_id,
            ),
        )
    )
    if not pairs:
        return _unavailable(currencies, "UNAVAILABLE")
    if any(pair.observed_at != pairs[0].observed_at for pair in pairs):
        return _unavailable(currencies, "UNALIGNED")
    with localcontext() as context:
        context.prec = 50
        return _aligned_strength(currencies, pairs)


def _aligned_strength(
    currencies: tuple[str, ...], pairs: tuple[PairHistory, ...]
) -> CurrencyStrength:
    basis, groups = _forest(currencies, pairs)
    basis_ids = tuple(pair.instrument_id for pair in basis)
    dependent = tuple(pair.instrument_id for pair in pairs if pair.instrument_id not in basis_ids)
    returns = {
        pair.instrument_id: tuple((price / pair.closes[0]).ln() * 10000 for price in pair.closes)
        for pair in pairs
    }
    estimates, residuals, components = [], [], []
    inconsistent = False
    for group in sorted(set(groups.values())):
        component = tuple(currency for currency in currencies if groups[currency] == group)
        components.append(component)
        if len(component) == 1:
            estimates.append(CurrencyEstimate(component[0], component, "UNAVAILABLE", None, ()))
            continue
        edges = tuple(pair for pair in basis if pair.base_currency in component)
        coefficients = _coefficients(component, edges)
        potentials = {
            currency: tuple(
                sum(
                    weight * returns[edge.instrument_id][index]
                    for edge, weight in zip(edges, weights)
                )
                for index in range(len(pairs[0].closes))
            )
            for currency, weights in coefficients.items()
        }
        component_residuals = tuple(
            PairResidual(
                pair.instrument_id,
                pair.instrument_id in dependent,
                max(
                    abs(observed - (base - quote))
                    for observed, base, quote in zip(
                        returns[pair.instrument_id],
                        potentials[pair.base_currency],
                        potentials[pair.quote_currency],
                    )
                ),
            )
            for pair in pairs
            if pair.base_currency in component
        )
        invalid = any(row.max_residual_bps > RESIDUAL_LIMIT_BPS for row in component_residuals)
        inconsistent |= invalid
        residuals.extend(component_residuals)
        for currency in component:
            decomposition = tuple(
                EdgeContribution(
                    edge.instrument_id,
                    weight,
                    returns[edge.instrument_id][-1],
                    weight * returns[edge.instrument_id][-1],
                )
                for edge, weight in zip(edges, coefficients[currency])
                if weight != 0
            )
            estimates.append(
                CurrencyEstimate(
                    currency,
                    component,
                    "WITHHELD" if invalid else "AVAILABLE",
                    None if invalid else potentials[currency][-1],
                    () if invalid else decomposition,
                )
            )
    status = "INCONSISTENT" if inconsistent else "AVAILABLE" if len(components) == 1 else "PARTIAL"
    return CurrencyStrength(
        status,
        tuple(sorted(estimates, key=lambda row: row.currency)),
        basis_ids,
        dependent,
        tuple(sorted(residuals, key=lambda row: row.instrument_id)),
        pairs[0].observed_at[0],
        pairs[0].observed_at[-1],
        len(pairs[0].closes),
    )
