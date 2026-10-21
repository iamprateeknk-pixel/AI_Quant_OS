"""Canonical quote ticks with duplicate detection and deterministic replay."""

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator

from ai_quant_os.contracts import _require_aware


@dataclass(frozen=True)
class Tick:
    instrument_id: str
    source_id: str
    sequence: int
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.observed_at)
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        if not self.instrument_id.strip() or not self.source_id.strip():
            raise ValueError("Tick instrument and source are required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("Tick sequence must be a nonnegative integer")
        if any(
            not value.is_finite() for value in (self.bid, self.ask, self.bid_size, self.ask_size)
        ):
            raise ValueError("Tick quote and sizes must be finite")
        if self.bid <= 0 or self.ask <= 0 or self.bid > self.ask:
            raise ValueError("Tick bid and ask are invalid")
        if self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("Tick sizes must be nonnegative")


class TickDataset:
    def __init__(self, ticks: Iterable[Tick]) -> None:
        by_key: dict[tuple[str, str, int], Tick] = {}
        duplicate_rows = 0
        for item in ticks:
            key = (item.instrument_id, item.source_id, item.sequence)
            previous = by_key.get(key)
            if previous is not None and previous != item:
                raise ValueError(f"Conflicting duplicate tick: {key}")
            if previous is not None:
                duplicate_rows += 1
            by_key[key] = item
        self.duplicate_rows = duplicate_rows
        self.ticks = tuple(
            sorted(
                by_key.values(),
                key=lambda item: (
                    item.observed_at,
                    item.instrument_id,
                    item.source_id,
                    item.sequence,
                ),
            )
        )

    @property
    def fingerprint(self) -> str:
        records = [
            [
                tick.instrument_id,
                tick.source_id,
                tick.sequence,
                tick.observed_at.isoformat(),
                str(tick.bid.normalize()),
                str(tick.ask.normalize()),
                str(tick.bid_size.normalize()),
                str(tick.ask_size.normalize()),
            ]
            for tick in self.ticks
        ]
        return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()

    def replay(self) -> Iterator[Tick]:
        return iter(self.ticks)

    def is_fresh(self, *, now: datetime, max_age: timedelta) -> bool:
        _require_aware(now)
        if max_age.total_seconds() < 0:
            raise ValueError("Maximum age cannot be negative")
        if not self.ticks:
            return False
        age = now - self.ticks[-1].observed_at
        return timedelta(0) <= age <= max_age


def import_ticks_csv(path: Path) -> TickDataset:
    return import_ticks_csv_bytes(path.read_bytes())


def import_ticks_csv_bytes(raw: bytes) -> TickDataset:
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    return TickDataset(
        Tick(
            instrument_id=row["instrument_id"],
            source_id=row["source_id"],
            sequence=int(row["sequence"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            bid=Decimal(row["bid"]),
            ask=Decimal(row["ask"]),
            bid_size=Decimal(row["bid_size"]),
            ask_size=Decimal(row["ask_size"]),
        )
        for row in rows
    )
