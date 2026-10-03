from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def measure(severity, seconds=0, kind="SCHEDULED"):
    from ai_quant_os.event_risk import EventObservation, event_measurements

    row = EventObservation(
        "event-1",
        kind,
        ("USD",),
        severity,
        NOW + timedelta(seconds=seconds),
        NOW - timedelta(seconds=1),
    )
    return event_measurements((row,), as_of=NOW)[0]


@pytest.mark.parametrize(
    "severity,seconds,score,blackout",
    [
        ("HIGH", 0, "1", True),
        ("HIGH", 450, "0.5", True),
        ("HIGH", -900, "0", True),
        ("HIGH", 901, "0", False),
        ("MEDIUM", 150, "0.25", True),
        ("MEDIUM", 301, "0", False),
        ("LOW", 0, "0.25", False),
        ("UNKNOWN", 0, None, None),
    ],
)
def test_declared_paper_windows_and_uncalibrated_severity_feature(
    severity, seconds, score, blackout
):
    result = measure(severity, seconds)
    assert result.blackout_recommended is blackout
    assert result.uncalibrated_score == (Decimal(score) if score is not None else None)
    assert result.seconds_until_event == seconds


def test_detected_events_cannot_claim_future_detection():
    with pytest.raises(ValueError):
        measure("HIGH", 1, "DETECTED")


def test_duplicate_event_identity_is_rejected():
    from ai_quant_os.event_risk import EventObservation, event_measurements

    row = EventObservation("event-1", "SCHEDULED", ("USD",), "HIGH", NOW, NOW)
    with pytest.raises(ValueError):
        event_measurements((row, row), as_of=NOW)


def test_repeated_local_hour_is_compared_in_utc_before_detection_validation():
    from ai_quant_os.event_risk import EventObservation

    class FoldZone(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=-5 if dt.fold else -4)

        def dst(self, dt):
            return timedelta(0)

    zone = FoldZone()
    occurred = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    reported = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    with pytest.raises(ValueError):
        EventObservation("detected", "DETECTED", ("USD",), "HIGH", occurred, reported)


@pytest.mark.parametrize("seconds", ["1e-1000000", "0e-1000000", "1." + "0" * 21])
def test_event_seconds_representation_cannot_expand_unbounded_output(seconds):
    from ai_quant_os.event_risk import paper_event_window

    with pytest.raises(ValueError):
        paper_event_window("HIGH", Decimal(seconds))
