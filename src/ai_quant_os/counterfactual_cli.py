"""Run declared alternatives against recorded holdout data as diagnostics only."""

import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(
        description="Counterfactual diagnostics on already-seen data; no promotion authority"
    )
    parser.add_argument("--workspace", type=Path, default=Path("data-local/research"))
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument(
        "--holdout", type=Path, help="Original holdout CSV for archives without retained bars"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from pydantic import TypeAdapter
        from ai_quant_os.counterfactual_contracts import CounterfactualPlan, ReplayVariant
        from ai_quant_os.counterfactual_results import ReplayInputs
        from ai_quant_os.counterfactual_pipeline import run_counterfactual
        from ai_quant_os.counterfactual_store import CounterfactualStore
        from ai_quant_os.data_archive import _write_immutable
        from ai_quant_os.market_data import import_bars_csv_bytes
        from ai_quant_os.research_candidate_store import CandidateStore
        from ai_quant_os.research_store import encode_research
        from ai_quant_os.strategy_registry import StrategyRegistry

        root, output = args.workspace.resolve(), args.output.resolve()
        if (
            output.suffix.lower() in (".sqlite3", ".sqlite", ".db")
            or output.name.endswith(("-wal", "-shm"))
            or output == args.variants.resolve()
            or (args.holdout is not None and output == args.holdout.resolve())
            or any(output.is_relative_to(root / folder) for folder in ("archives", "strategies"))
        ):
            raise ValueError("Output cannot overwrite research storage or inputs")
        if args.variants.stat().st_size > 64 * 1024:
            raise ValueError("Variant declaration exceeds capacity")
        raw = args.variants.read_bytes()
        if len(raw) > 64 * 1024:
            raise ValueError("Variant declaration exceeds capacity")
        declared = json.loads(raw)
        if not isinstance(declared, list) or not 1 <= len(declared) <= 7:
            raise ValueError("Declare one to seven alternatives")
        for row in declared:
            if (
                not isinstance(row, dict)
                or set(row) != {"name", "genome", "quantity", "costs"}
                or not isinstance(row["genome"], dict)
                or set(row["genome"]) != {"strategy_ref", "input_kind", "parameters"}
                or not isinstance(row["costs"], dict)
                or set(row["costs"]) != {"slippage_bps", "fee_per_trade"}
                or type(row["quantity"]) is not int
            ):
                raise ValueError("Variant declaration contains unknown or invalid fields")
        variants = TypeAdapter(tuple[ReplayVariant, ...]).validate_json(raw, strict=True)
        candidate = CandidateStore(root / "candidates.sqlite3").bundle(args.candidate_id)
        if candidate is None or candidate.report.holdout is None:
            raise ValueError("A candidate with recorded native holdout evidence is required")
        if args.holdout is not None:
            if args.holdout.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Holdout CSV exceeds capacity")
            raw_source = args.holdout.read_bytes()
            if len(raw_source) > 32 * 1024 * 1024:
                raise ValueError("Holdout CSV exceeds capacity")
            dataset = import_bars_csv_bytes(raw_source)
            if dataset.duplicate_rows:
                raise ValueError("Duplicate holdout observations are not accepted")
            bars = dataset.bars
        elif candidate.report.adversarial is not None:
            bars = candidate.report.adversarial.source_bars
        else:
            raise ValueError("This archive requires the original holdout CSV")
        plan = CounterfactualPlan(
            args.replay_id,
            candidate.manifest.candidate_id,
            candidate.manifest.report_sha256,
            candidate.report.plan.holdout_fingerprint,
            variants,
        )
        inputs = ReplayInputs(plan, candidate, bars)
        if output.exists() and output.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Existing output exceeds capacity")
        report = run_counterfactual(
            inputs,
            StrategyRegistry(root / "strategies"),
            CounterfactualStore(root / "counterfactual.sqlite3"),
        )
        _write_immutable(output, encode_research(report))
        print(f"{report.outcome}: already-seen-data diagnostics exported; promotion_allowed=false")
    except ImportError:
        parser.error('Install research prerequisites: python -m pip install -e ".[data,trading]"')
    except (ValueError, OSError, sqlite3.Error, ArithmeticError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
