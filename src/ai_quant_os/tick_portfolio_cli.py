"""Bounded immutable export of a trusted asynchronous native quote replay."""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_quant_os.data_archive import _write_immutable
from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.tick_data import import_ticks_csv_bytes


class TickLegFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: Path
    genome: StrategyGenome
    quantity: int = Field(strict=True, ge=1, le=500000)


class TickPortfolioFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    legs: tuple[TickLegFile, ...] = Field(min_length=2, max_length=4)
    initial_balance_usd: Decimal = Decimal(100000)
    fee_per_order_usd: Decimal = Decimal(0)
    max_quote_age_seconds: int = Field(default=3, strict=True, ge=1, le=300)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate plan key")
        result[key] = value
    return result


def _read_bounded(path, maximum):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Portfolio input exceeds byte capacity")
    return raw


def main():
    parser = argparse.ArgumentParser(description="Native asynchronous recorded-quote diagnostic")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from ai_quant_os.native_tick_portfolio import (
            TickPortfolioInput,
            TickPortfolioReport,
            run_native_tick_portfolio,
        )
        from ai_quant_os.strategy_registry import StrategyRegistry

        plan_path, output = args.plan.resolve(), args.output.resolve()
        raw_plan = _read_bounded(plan_path, 65536)
        json.loads(raw_plan, object_pairs_hook=_unique_object)
        plan = TickPortfolioFiles.model_validate_json(raw_plan)
        paths = tuple((plan_path.parent / leg.csv).resolve() for leg in plan.legs)
        registry_path = (args.registry or output.parent / ".strategy-registry").resolve()
        if output in (plan_path, *paths) or output.is_relative_to(registry_path):
            raise ValueError("Portfolio output cannot overwrite inputs or registry")
        if any(path.is_relative_to(registry_path) for path in (plan_path, *paths)):
            raise ValueError("Portfolio registry cannot contain inputs")
        inputs, lineage = [], []
        for leg, path in zip(plan.legs, paths, strict=True):
            raw = _read_bounded(path, 4 * 1024 * 1024)
            dataset = import_ticks_csv_bytes(raw)
            if not dataset.ticks:
                raise ValueError("Portfolio source is empty")
            inputs.append(TickPortfolioInput(dataset, leg.genome, leg.quantity))
            lineage.append(
                {
                    "instrument_id": dataset.ticks[0].instrument_id,
                    "source_sha256": sha256(raw).hexdigest(),
                }
            )
        report = run_native_tick_portfolio(
            tuple(inputs),
            StrategyRegistry(registry_path),
            initial_balance_usd=plan.initial_balance_usd,
            fee_per_order_usd=plan.fee_per_order_usd,
            max_quote_age_seconds=plan.max_quote_age_seconds,
        )
        payload = {
            "report": TypeAdapter(TickPortfolioReport).dump_python(report, mode="json"),
            "source_lineage": sorted(lineage, key=lambda row: row["instrument_id"]),
        }
        encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        _write_immutable(output, encoded)
        print("Native tick portfolio exported; promotion_allowed=false")
    except ImportError:
        parser.error('Install the native prerequisite: python -m pip install -e ".[trading]"')
    except (ValueError, OSError, ArithmeticError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
