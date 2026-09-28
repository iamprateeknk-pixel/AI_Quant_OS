import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator

from ai_quant_os.contracts import _require_aware


@dataclass(frozen=True)
class Bar:
    instrument_id: str
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.observed_at)
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        prices = (self.open, self.high, self.low, self.close)
        if not self.instrument_id or any(not price.is_finite() or price <= 0 for price in prices):
            raise ValueError("Invalid bar instrument or price")
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close, self.high
        ):
            raise ValueError("Bar OHLC is inconsistent")
        if not self.volume.is_finite() or self.volume < 0:
            raise ValueError("Bar volume must be finite and nonnegative")


class BarDataset:
    def __init__(self, bars: Iterable[Bar]) -> None:
        by_key: dict[tuple[str, datetime], Bar] = {}
        duplicate_rows = 0
        for item in bars:
            key = (item.instrument_id, item.observed_at)
            previous = by_key.get(key)
            if previous is not None and previous != item:
                raise ValueError(
                    f"Conflicting duplicate bar: {item.instrument_id} {item.observed_at}"
                )
            if previous is not None:
                duplicate_rows += 1
            by_key[key] = item
        self.duplicate_rows = duplicate_rows
        self.bars = tuple(
            sorted(by_key.values(), key=lambda item: (item.observed_at, item.instrument_id))
        )

    @property
    def fingerprint(self) -> str:
        records = [
            [
                bar.instrument_id,
                bar.observed_at.isoformat(),
                str(bar.open.normalize()),
                str(bar.high.normalize()),
                str(bar.low.normalize()),
                str(bar.close.normalize()),
                str(bar.volume.normalize()),
            ]
            for bar in self.bars
        ]
        return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()

    def replay(self) -> Iterator[Bar]:
        return iter(self.bars)


def import_bars_csv(path: Path) -> BarDataset:
    return import_bars_csv_bytes(path.read_bytes())


def import_bars_csv_bytes(raw: bytes) -> BarDataset:
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    return BarDataset(
        Bar(
            instrument_id=row["instrument_id"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        for row in rows
    )
