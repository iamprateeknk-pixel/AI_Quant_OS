"""Strict bounded research CSV parsing shared by intake and validation snapshots."""

import csv
from decimal import InvalidOperation
import io

from ai_quant_os.market_data import BarDataset, import_bars_csv_bytes
from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS


def parse_research_csv(raw: bytes) -> BarDataset:
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError("Research CSV byte bound exceeded")
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
        header = next(rows, None)
        if header != ["instrument_id", "observed_at", "open", "high", "low", "close", "volume"]:
            raise ValueError("Unsupported research CSV columns")
        for count, row in enumerate(rows, start=1):
            if count > 4096 or len(row) != 7 or any(not value for value in row):
                raise ValueError("Invalid research CSV row shape or count")
        dataset = import_bars_csv_bytes(raw)
    except (KeyError, TypeError, UnicodeError, csv.Error, InvalidOperation) as error:
        raise ValueError("Invalid research CSV") from error
    if (
        not 2 <= len(dataset.bars) <= 4096
        or dataset.duplicate_rows
        or len({row.instrument_id for row in dataset.bars}) != 1
        or dataset.bars[0].instrument_id not in {row.instrument_id for row in PAPER_INSTRUMENTS}
    ):
        raise ValueError("Research requires unique bounded single-instrument observations")
    return dataset
