from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ai_quant_os.market_data import Bar, BarDataset, import_bars_csv


def bar(timestamp: datetime, close: str = "1.10") -> Bar:
    return Bar(
        instrument_id="EURUSD",
        observed_at=timestamp,
        open=Decimal("1.09"),
        high=Decimal("1.11"),
        low=Decimal("1.08"),
        close=Decimal(close),
        volume=Decimal("100"),
    )


def test_bar_timestamps_are_normalized_to_utc() -> None:
    source = datetime.fromisoformat("2026-01-01T05:30:00+05:30")
    dataset = BarDataset([bar(source)])
    assert dataset.bars[0].observed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_identical_duplicate_bar_is_deduplicated() -> None:
    sample = bar(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(BarDataset([sample, sample]).bars) == 1


def test_conflicting_duplicate_bar_is_rejected() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Conflicting"):
        BarDataset([bar(timestamp), bar(timestamp, close="1.105")])


def test_replay_and_fingerprint_are_independent_of_input_order() -> None:
    earlier = bar(datetime(2026, 1, 1, tzinfo=timezone.utc))
    later = bar(datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), close="1.105")
    forward = BarDataset([earlier, later])
    reverse = BarDataset([later, earlier])
    assert forward.bars == reverse.bars
    assert forward.fingerprint == reverse.fingerprint


def test_csv_import_rejects_naive_timestamp(tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "observed_at,instrument_id,open,high,low,close,volume\n"
        "2026-01-01T00:00:00,EURUSD,1.09,1.11,1.08,1.10,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="timezone"):
        import_bars_csv(source)
