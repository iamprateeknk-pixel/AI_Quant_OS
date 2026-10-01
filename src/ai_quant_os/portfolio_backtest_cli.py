"""Bounded source-file command for a trusted native multi-instrument portfolio."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ai_quant_os.market_data import import_bars_csv_bytes
from ai_quant_os.strategy_contracts import StrategyGenome


class LegFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: Path
    genome: StrategyGenome
    quantity: int = Field(strict=True, ge=1, le=1000000)
    slippage_bps: Decimal = Decimal(0)


class PortfolioFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    legs: tuple[LegFile, ...] = Field(min_length=2, max_length=4)
    initial_balance_usd: Decimal = Decimal(100000)
    fee_per_order_usd: Decimal = Decimal(0)


def main():
    parser = argparse.ArgumentParser(
        description="Native shared-USD portfolio diagnostic; no order authority"
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        from ai_quant_os.native_portfolio import (
            PortfolioInput,
            PortfolioReport,
            run_native_portfolio,
        )
        from ai_quant_os.strategy_registry import StrategyRegistry

        plan_path, output = args.plan.resolve(), args.output.resolve()
        registry_path = (args.registry or output.parent / ".strategy-registry").resolve()
        if plan_path.stat().st_size > 1024 * 1024:
            raise ValueError("Portfolio plan exceeds byte capacity")
        plan = PortfolioFiles.model_validate_json(plan_path.read_bytes())
        paths = tuple((plan_path.parent / row.csv).resolve() for row in plan.legs)
        if output in (plan_path, *paths) or output.is_relative_to(registry_path):
            raise ValueError("Portfolio output cannot overwrite input or registry storage")
        inputs, lineage = [], []
        for row, path in zip(plan.legs, paths, strict=True):
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Portfolio CSV exceeds byte capacity")
            raw = path.read_bytes()
            dataset = import_bars_csv_bytes(raw)
            inputs.append(PortfolioInput(dataset, row.genome, row.quantity, row.slippage_bps))
            lineage.append(
                {
                    "instrument_id": dataset.bars[0].instrument_id,
                    "source_sha256": sha256(raw).hexdigest(),
                }
            )
        report = run_native_portfolio(
            tuple(inputs),
            StrategyRegistry(registry_path),
            fee_per_order_usd=plan.fee_per_order_usd,
            initial_balance_usd=plan.initial_balance_usd,
        )
        record = TypeAdapter(PortfolioReport).dump_python(report, mode="json")
        payload = {
            "report": record,
            "source_lineage": sorted(lineage, key=lambda row: row["instrument_id"]),
        }
        encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        if output.exists() and output.read_bytes() != encoded:
            raise ValueError("Portfolio export is immutable; choose a new output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        print("Native portfolio diagnostic exported; promotion_allowed=false")
    except ImportError:
        parser.error('Install the native prerequisite: python -m pip install -e ".[trading]"')
    except (ValueError, OSError, ArithmeticError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
