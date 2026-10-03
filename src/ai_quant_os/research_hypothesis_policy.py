"""Predeclared descriptive direction screen; no significance or trading authority."""

from decimal import Decimal
from typing import Literal

ResearchRule = Literal["FOLLOW_PREVIOUS_CLOSE_DIRECTION", "FADE_PREVIOUS_CLOSE_DIRECTION"]


def direction_counts(closes: tuple[Decimal, ...]) -> tuple[int, int]:
    if (
        type(closes) is not tuple
        or not 2 <= len(closes) <= 4096
        or any(
            type(value) is not Decimal or not value.is_finite() or value <= 0 for value in closes
        )
    ):
        raise ValueError("Invalid research close observations")
    directions = tuple(
        1 if current > prior else -1 if current < prior else 0
        for prior, current in zip(closes, closes[1:])
    )
    pairs = tuple(
        (first, second) for first, second in zip(directions, directions[1:]) if first and second
    )
    return len(pairs), sum(first == second for first, second in pairs)


def research_rule(pairs: int, same: int) -> ResearchRule | None:
    if type(pairs) is not int or type(same) is not int or not 0 <= same <= pairs <= 4094:
        raise ValueError("Invalid research direction counts")
    if pairs < 8:
        return None
    if 4 * same >= 3 * pairs:
        return "FOLLOW_PREVIOUS_CLOSE_DIRECTION"
    if 4 * same <= pairs:
        return "FADE_PREVIOUS_CLOSE_DIRECTION"
    return None
