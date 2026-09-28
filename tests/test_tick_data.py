from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_quant_os.tick_data import Tick, TickDataset, import_ticks_csv_bytes


def tick(sequence: int, at: str = "2026-01-01T05:30:00+05:30") -> Tick:
    return Tick(
        instrument_id="EURUSD",
        source_id="fixture",
        sequence=sequence,
        observed_at=datetime.fromisoformat(at),
        bid=Decimal("1.09"),
        ask=Decimal("1.10"),
        bid_size=Decimal("100"),
        ask_size=Decimal("200"),
    )


def test_identical_ticks_deduplicate_and_replay_in_utc() -> None:
    first = tick(1)
    second = tick(2, "2026-01-01T00:00:01+00:00")
    dataset = TickDataset([second, first, first])
    assert dataset.duplicate_rows == 1
    assert list(dataset.replay()) == [first, second]
    assert dataset.ticks[0].observed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert dataset.fingerprint == TickDataset([first, second]).fingerprint


def test_same_source_sequence_with_conflicting_payload_fails() -> None:
    with pytest.raises(ValueError, match="Conflicting duplicate tick"):
        TickDataset([tick(1), replace(tick(1), bid=Decimal("1.08"))])


def test_same_timestamp_different_sequences_are_kept() -> None:
    assert len(TickDataset([tick(1), tick(2)]).ticks) == 2


def test_tick_validation_rejects_crossed_or_nonfinite_quotes_and_naive_time() -> None:
    with pytest.raises(ValueError, match="bid and ask"):
        replace(tick(1), bid=Decimal("1.11"))
    with pytest.raises(ValueError, match="finite"):
        replace(tick(1), ask_size=Decimal("NaN"))
    with pytest.raises(ValueError, match="timezone"):
        replace(tick(1), observed_at=datetime(2026, 1, 1))


def test_tick_fingerprint_uses_numeric_value_and_freshness() -> None:
    sample = tick(1)
    assert (
        TickDataset([sample]).fingerprint
        == TickDataset([replace(sample, bid=Decimal("1.0900"))]).fingerprint
    )
    dataset = TickDataset([sample])
    assert dataset.is_fresh(
        now=sample.observed_at + timedelta(seconds=5), max_age=timedelta(seconds=5)
    )
    assert not dataset.is_fresh(
        now=sample.observed_at + timedelta(seconds=6), max_age=timedelta(seconds=5)
    )
    assert not dataset.is_fresh(
        now=sample.observed_at - timedelta(seconds=1), max_age=timedelta(seconds=5)
    )


def test_csv_import_requires_aware_timestamps() -> None:
    raw = (
        b"instrument_id,source_id,sequence,observed_at,bid,ask,bid_size,ask_size\n"
        b"EURUSD,fixture,1,2026-01-01T00:00:00,1.09,1.10,100,200\n"
    )
    with pytest.raises(ValueError, match="timezone"):
        import_ticks_csv_bytes(raw)
