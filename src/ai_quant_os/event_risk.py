"""Descriptive event proximity under an explicit, uncalibrated paper policy."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
import re
from typing import Literal

from ai_quant_os.contracts import _require_aware


@dataclass(frozen=True)
class EventObservation:
    event_id: str
    kind: Literal["SCHEDULED", "DETECTED"]
    currencies: tuple[str, ...]
    severity: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    occurs_at: datetime
    reported_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.occurs_at)
        _require_aware(self.reported_at)
        try:
            object.__setattr__(self, "occurs_at", self.occurs_at.astimezone(timezone.utc))
            object.__setattr__(self, "reported_at", self.reported_at.astimezone(timezone.utc))
        except OverflowError as error:
            raise ValueError("Event timestamp is outside UTC range") from error
        if (
            re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.event_id) is None
            or self.kind not in {"SCHEDULED", "DETECTED"}
            or self.severity not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
            or not isinstance(self.currencies, tuple)
            or not 1 <= len(self.currencies) <= 16
            or len(set(self.currencies)) != len(self.currencies)
            or any(re.fullmatch(r"[A-Z]{3}", value) is None for value in self.currencies)
            or (self.kind == "DETECTED" and self.occurs_at > self.reported_at)
        ):
            raise ValueError("Invalid event observation")
        object.__setattr__(self, "currencies", tuple(sorted(self.currencies)))


@dataclass(frozen=True)
class EventMeasurement:
    event_id: str
    kind: Literal["SCHEDULED", "DETECTED"]
    currencies: tuple[str, ...]
    severity: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    seconds_until_event: Decimal
    window_seconds: int | None
    uncalibrated_score: Decimal | None
    blackout_recommended: bool | None


@dataclass(frozen=True)
class PaperEventWindow:
    window_seconds: int | None
    uncalibrated_score: Decimal | None
    blackout_recommended: bool | None


def paper_event_window(
    severity: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"], seconds: Decimal
) -> PaperEventWindow:
    if (
        severity not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
        or not isinstance(seconds, Decimal)
        or not seconds.is_finite()
        or not Decimal("-1e12") <= seconds <= Decimal("1e12")
        or len(seconds.as_tuple().digits) > 20
        or not -6 <= seconds.as_tuple().exponent <= 12
    ):
        raise ValueError("Invalid bounded event proximity")
    with localcontext(Context(prec=50)):
        window = 300 if severity == "MEDIUM" else 900
        weight = {"LOW": Decimal("0.25"), "MEDIUM": Decimal("0.5"), "HIGH": Decimal(1)}.get(
            severity
        )
        score = max(Decimal(0), 1 - abs(seconds) / window) * weight if weight is not None else None
        blackout = (
            abs(seconds) <= window
            if severity in {"HIGH", "MEDIUM"}
            else False
            if severity == "LOW"
            else None
        )
        return PaperEventWindow(window if weight is not None else None, score, blackout)


def event_measurements(
    events: tuple[EventObservation, ...], *, as_of: datetime
) -> tuple[EventMeasurement, ...]:
    _require_aware(as_of)
    if (
        not isinstance(events, tuple)
        or len(events) > 128
        or any(
            not isinstance(row, EventObservation)
            or row.reported_at > as_of
            or (row.kind == "DETECTED" and row.occurs_at > as_of)
            for row in events
        )
        or len({row.event_id for row in events}) != len(events)
    ):
        raise ValueError("Invalid bounded event evidence")
    results = []
    with localcontext(Context(prec=50)):
        for row in sorted(events, key=lambda item: (item.occurs_at, item.event_id)):
            gap = row.occurs_at - as_of.astimezone(timezone.utc)
            seconds = Decimal(gap.days * 86400 + gap.seconds) + Decimal(gap.microseconds) / 1000000
            feature = paper_event_window(row.severity, seconds)
            results.append(
                EventMeasurement(
                    row.event_id,
                    row.kind,
                    row.currencies,
                    row.severity,
                    seconds,
                    feature.window_seconds,
                    feature.uncalibrated_score,
                    feature.blackout_recommended,
                )
            )
    return tuple(results)
