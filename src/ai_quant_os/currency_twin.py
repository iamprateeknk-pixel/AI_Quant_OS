"""Currency Digital Twins (V3.2 Master Blueprint, section 17).

Each currency carries a per-currency state vector ("twin") built from eight
content components plus a computed confidence:

    policy       central-bank stance, rate path, statement surprise
    macro        inflation, labour, growth, PMI, trade / current account
    rates        curve, real-rate proxies, cross-country rate differential
    price_state  trend / momentum / mean-reversion pressure
    volatility   realised, implied proxies, jump risk
    liquidity    spread, depth, session, rollover behaviour
    positioning  reliable flow / positioning proxies
    narrative    material macro / geopolitical themes

``pair_score`` implements the blueprint pair formula:

    PAIR_SCORE(A/B) = f(TWIN_A - TWIN_B, pair_microstructure, regime_state,
                        event_state, costs, confidence)

concretely, in this module:

    combined_confidence = min(confidence_A, confidence_B)
    score = (score_A - score_B) * combined_confidence
            + microstructure + regime_state - event_state - costs

where ``score_X`` is the confidence-weighted mean of twin X's component
values. If ``combined_confidence`` falls below the confidence floor the pair
score is exactly zero: low confidence means no trade signal, because cash is
a valid position (blueprint sections 17 and 27).

This module is pure and deterministic: standard library only, ``Decimal``
arithmetic, no I/O, no clocks. Freshness is supplied by the caller as an age
in seconds so results stay replayable.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# The eight content components of a twin, in blueprint section 17 order.
REQUIRED_COMPONENTS: tuple[str, ...] = (
    "policy",
    "macro",
    "rates",
    "price_state",
    "volatility",
    "liquidity",
    "positioning",
    "narrative",
)

# A component older than this is stale: its confidence contribution halves.
STALE_THRESHOLD_SECONDS = 3600

_CURRENCY_CODE = re.compile(r"[A-Z]{3}")
_HALF = Decimal("0.5")
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True)
class TwinComponent:
    """One signed piece of twin evidence for a single currency.

    ``value`` is a signed score in [-1, +1] (negative = bearish pressure on
    the currency, positive = bullish). ``confidence`` is in [0, 1].
    ``freshness_seconds`` is the age of the underlying observation (>= 0).
    """

    name: str
    value: Decimal
    confidence: Decimal
    freshness_seconds: int
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Component name must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Component source must be a non-empty string")
        if type(self.freshness_seconds) is not int or self.freshness_seconds < 0:
            raise ValueError("Freshness must be a non-negative integer number of seconds")
        for label, number in (("value", self.value), ("confidence", self.confidence)):
            if not isinstance(number, Decimal) or not number.is_finite():
                raise ValueError(f"Component {label} must be a finite Decimal")
        if not -_ONE <= self.value <= _ONE:
            raise ValueError("Component value must lie in [-1, +1]")
        if not _ZERO <= self.confidence <= _ONE:
            raise ValueError("Component confidence must lie in [0, 1]")

    @property
    def is_stale(self) -> bool:
        return self.freshness_seconds > STALE_THRESHOLD_SECONDS

    @property
    def effective_confidence(self) -> Decimal:
        """Confidence after the staleness penalty (halved when stale)."""
        if self.is_stale:
            return self.confidence * _HALF
        return self.confidence


@dataclass(frozen=True)
class CurrencyTwin:
    """A complete twin for one currency: every required component once.

    Construction validates coverage: exactly the components in
    ``REQUIRED_COMPONENTS``, no duplicates and no unknown names, so a twin
    can never silently omit a section of the blueprint state vector.
    """

    currency: str
    components: tuple[TwinComponent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not _CURRENCY_CODE.fullmatch(self.currency):
            raise ValueError("Currency must be a three-letter uppercase code")
        if not isinstance(self.components, tuple) or not self.components:
            raise ValueError("Components must be a non-empty tuple of TwinComponent")
        if not all(isinstance(item, TwinComponent) for item in self.components):
            raise ValueError("Components must all be TwinComponent instances")
        names = tuple(item.name for item in self.components)
        if len(set(names)) != len(names):
            raise ValueError("Twin components must not repeat a name")
        unknown = set(names) - set(REQUIRED_COMPONENTS)
        if unknown:
            raise ValueError(f"Unknown twin components: {sorted(unknown)}")
        missing = set(REQUIRED_COMPONENTS) - set(names)
        if missing:
            raise ValueError(f"Twin is missing components: {sorted(missing)}")

    def component(self, name: str) -> TwinComponent:
        """Return the named component, raising ``KeyError`` when absent."""
        for item in self.components:
            if item.name == name:
                return item
        raise KeyError(f"Twin for {self.currency} has no component {name!r}")

    def weighted_score(self) -> Decimal:
        """Confidence-weighted mean of component values, in [-1, +1].

        Each component contributes ``value * confidence`` and the total is
        normalised by the summed confidence. A twin whose components all
        carry zero confidence scores exactly zero (no evidence, no signal).
        """
        total_weight = sum((item.confidence for item in self.components), _ZERO)
        if total_weight == _ZERO:
            return _ZERO
        weighted = sum((item.value * item.confidence for item in self.components), _ZERO)
        return weighted / total_weight

    def overall_confidence(self) -> Decimal:
        """Mean confidence, with stale components contributing only half.

        A component is stale when ``freshness_seconds`` exceeds
        ``STALE_THRESHOLD_SECONDS`` (3600); its contribution to the mean is
        halved, so old evidence drags overall confidence down even when the
        originally reported confidence was high.
        """
        total = sum((item.effective_confidence for item in self.components), _ZERO)
        return total / Decimal(len(self.components))


def pair_score(
    twin_a: CurrencyTwin,
    twin_b: CurrencyTwin,
    *,
    microstructure: Decimal,
    regime_state: Decimal,
    event_state: Decimal,
    costs: Decimal,
    confidence_floor: Decimal = Decimal("0.3"),
) -> Decimal:
    """Score pair A/B: how strongly the twins favour A over B.

    Formula (blueprint section 17)::

        PAIR_SCORE(A/B) = f(TWIN_A - TWIN_B, pair_microstructure,
                            regime_state, event_state, costs, confidence)

        combined_confidence = min(twin_a.overall_confidence(),
                                  twin_b.overall_confidence())
        score = (twin_a.weighted_score() - twin_b.weighted_score())
                * combined_confidence
                + microstructure + regime_state - event_state - costs

    The twin difference is scaled by the *weaker* twin's confidence, so one
    unreliable leg mutes the whole signal. Positive adjustments come from
    pair microstructure quality and a supportive regime state; event risk
    and trading costs drag the score down. If ``combined_confidence`` is
    below ``confidence_floor`` the result is exactly ``Decimal(0)``: no
    trade signal when confidence is too low, because cash is a position.
    """
    adjustments = {
        "microstructure": microstructure,
        "regime_state": regime_state,
        "event_state": event_state,
        "costs": costs,
        "confidence_floor": confidence_floor,
    }
    for label, number in adjustments.items():
        if not isinstance(number, Decimal) or not number.is_finite():
            raise ValueError(f"Pair score {label} must be a finite Decimal")
    if not _ZERO <= confidence_floor <= _ONE:
        raise ValueError("Confidence floor must lie in [0, 1]")
    if costs < _ZERO:
        raise ValueError("Pair score costs must be non-negative")
    combined_confidence = min(twin_a.overall_confidence(), twin_b.overall_confidence())
    if combined_confidence < confidence_floor:
        return _ZERO
    twin_difference = twin_a.weighted_score() - twin_b.weighted_score()
    signal = twin_difference * combined_confidence
    return signal + microstructure + regime_state - event_state - costs


def _as_decimal(raw: object, label: str) -> Decimal:
    if isinstance(raw, Decimal):
        return raw
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Twin field {label} is not a valid number: {raw!r}") from None


def build_twin(currency: str, raw: dict) -> CurrencyTwin:
    """Build a ``CurrencyTwin`` from a plain dict of component dicts.

    Expected shape — one entry per name in ``REQUIRED_COMPONENTS``::

        {
            "policy": {"value": "0.4", "confidence": "0.8",
                       "freshness_seconds": 120, "source": "central-bank-feed"},
            ...
        }

    ``value``/``confidence`` may be ``Decimal``, ``int``, ``float`` or a
    numeric string. Raises ``ValueError`` when a required component is
    missing, an entry is not a dict, a field is absent, or a field cannot
    be converted or validated.
    """
    if not isinstance(raw, dict):
        raise ValueError("Twin data must be a dict of component dicts")
    missing = [name for name in REQUIRED_COMPONENTS if name not in raw]
    if missing:
        raise ValueError(f"Twin data is missing components: {missing}")
    components: list[TwinComponent] = []
    for name in REQUIRED_COMPONENTS:
        entry = raw[name]
        if not isinstance(entry, dict):
            raise ValueError(f"Twin component {name!r} must be a dict of fields")
        try:
            value = _as_decimal(entry["value"], f"{name}.value")
            confidence = _as_decimal(entry["confidence"], f"{name}.confidence")
            freshness = entry["freshness_seconds"]
            source = entry["source"]
        except KeyError as error:
            raise ValueError(f"Twin component {name!r} is missing field {error}") from None
        if isinstance(freshness, bool) or not isinstance(freshness, int):
            try:
                freshness = int(freshness)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Twin component {name!r} freshness_seconds is not an integer"
                ) from None
        components.append(
            TwinComponent(
                name=name,
                value=value,
                confidence=confidence,
                freshness_seconds=freshness,
                source=source,
            )
        )
    return CurrencyTwin(currency=currency, components=tuple(components))


__all__ = [
    "REQUIRED_COMPONENTS",
    "STALE_THRESHOLD_SECONDS",
    "CurrencyTwin",
    "TwinComponent",
    "build_twin",
    "pair_score",
]
