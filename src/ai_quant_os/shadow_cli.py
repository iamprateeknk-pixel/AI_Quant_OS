"""Local operator commands for isolated candidate shadow portfolios."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(
        description="Candidate shadow diagnostics with no broker authority"
    )
    parser.add_argument("--workspace", type=Path, default=Path("data-local/research"))
    commands = parser.add_subparsers(dest="command", required=True)
    assign = commands.add_parser("assign")
    assign.add_argument("shadow_id")
    assign.add_argument("--candidate-id", required=True)
    assign.add_argument("--reality-gap-threshold-bps", type=Decimal)
    assign.add_argument("--minimum-shadow-bars", type=int, default=60)
    assign.add_argument("--minimum-shadow-fills", type=int, default=2)
    assign.add_argument("--minimum-holdout-fills", type=int, default=2)
    observe = commands.add_parser("observe")
    observe.add_argument("shadow_id")
    observe.add_argument("--command-id", required=True)
    observe.add_argument("--bars", type=Path, required=True)
    latest = commands.add_parser("latest")
    latest.add_argument("shadow_id")
    latest.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from ai_quant_os.data_archive import _write_immutable
        from ai_quant_os.market_data import import_bars_csv_bytes
        from ai_quant_os.research_candidate_store import CandidateStore
        from ai_quant_os.research_store import encode_research
        from ai_quant_os.shadow_pipeline import assign_shadow, observe_shadow
        from ai_quant_os.shadow_reality_gap import RealityGapPolicy, assess_shadow_reality_gap
        from ai_quant_os.shadow_portfolio import ShadowAssignment
        from ai_quant_os.shadow_store import ShadowStore
        from ai_quant_os.strategy_registry import StrategyRegistry

        root = args.workspace.resolve()
        store = ShadowStore(root / "shadow.sqlite3")
        candidates = CandidateStore(root / "candidates.sqlite3")
        strategies = StrategyRegistry(root / "strategies")
        if args.command == "assign":
            assignment = store.lookup(args.shadow_id)
            if (
                assignment is not None
                and assignment.candidate.manifest.candidate_id != args.candidate_id
            ):
                raise ValueError("Immutable shadow candidate assignment cannot change")
            reality_gap_policy = (
                RealityGapPolicy(
                    args.minimum_shadow_bars,
                    args.minimum_shadow_fills,
                    args.minimum_holdout_fills,
                    args.reality_gap_threshold_bps,
                )
                if args.reality_gap_threshold_bps is not None
                else None
            )
            if assignment is None:
                snapshot = candidates.snapshot(args.candidate_id)
                if snapshot is None:
                    raise ValueError("Unknown research candidate")
                view, candidate = snapshot
                assignment = ShadowAssignment(
                    args.shadow_id, datetime.now(timezone.utc), view, candidate
                )
            print(
                encode_research(
                    assign_shadow(
                        assignment,
                        store,
                        candidates,
                        strategies,
                        reality_gap_policy=reality_gap_policy,
                    )
                ).decode()
            )
        elif args.command == "observe":
            if args.bars.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Observation CSV exceeds capacity")
            raw = args.bars.read_bytes()
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError("Observation CSV exceeds capacity")
            source = import_bars_csv_bytes(raw)
            if source.duplicate_rows:
                raise ValueError("Duplicate shadow observations are refused")
            report = observe_shadow(
                args.shadow_id,
                args.command_id,
                source.bars,
                store,
                candidates,
                strategies,
            )
            status = ""
            reality_gap_policy = store.reality_gap_policy(args.shadow_id)
            if reality_gap_policy is not None:
                assessment = assess_shadow_reality_gap(report, reality_gap_policy)
                view = candidates.get(report.assignment.candidate.manifest.candidate_id)
                status = f"; reality_gap={assessment.state}; candidate_state={view.state}"
                if assessment.reasons:
                    status += f"; reasons={','.join(assessment.reasons)}"
            print(
                f"{report.outcome}: shadow sequence {report.sequence}{status}; promotion_allowed=false"
            )
        else:
            output = args.output.resolve()
            if (
                output.suffix.lower() in (".sqlite3", ".sqlite", ".db")
                or output.name.endswith(("-wal", "-shm"))
                or any(
                    output.is_relative_to(root / folder) for folder in ("archives", "strategies")
                )
            ):
                raise ValueError("Output cannot overwrite research storage")
            report = store.latest(args.shadow_id)
            if report is None:
                raise ValueError("Shadow portfolio has no completed observation")
            if output.exists() and output.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Existing output exceeds capacity")
            _write_immutable(output, encode_research(report))
            print("Shadow evidence exported; promotion_allowed=false")
    except ImportError:
        parser.error('Install research prerequisites: python -m pip install -e ".[data,trading]"')
    except (ValueError, OSError, sqlite3.Error, ArithmeticError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
