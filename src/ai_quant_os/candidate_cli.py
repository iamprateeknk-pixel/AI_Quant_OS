"""Register, inspect, export or withdraw local evidence-only research candidates."""

import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description="Local research dossiers; no promotion authority")
    parser.add_argument("--workspace", type=Path, default=Path("data-local/research"))
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("experiment_id")
    commands.add_parser("list")
    bundle = commands.add_parser("bundle")
    bundle.add_argument("candidate_id")
    bundle.add_argument("--output", type=Path, required=True)
    withdraw = commands.add_parser("withdraw")
    withdraw.add_argument("candidate_id")
    withdraw.add_argument("--expected-revision", type=int, required=True)
    withdraw.add_argument("--command-id", required=True)
    withdraw.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        from ai_quant_os.data_archive import _write_immutable
        from ai_quant_os.research_candidates import build_candidate_bundle
        from ai_quant_os.research_candidate_store import CandidateStore
        from ai_quant_os.research_store import ResearchStore, encode_research
        from ai_quant_os.strategy_registry import StrategyRegistry

        root = args.workspace.resolve()
        store = CandidateStore(root / "candidates.sqlite3")
        if args.command == "register":
            dossier = build_candidate_bundle(
                ResearchStore(root / "research.sqlite3"),
                StrategyRegistry(root / "strategies"),
                args.experiment_id,
            )
            print(encode_research(store.register(dossier)).decode())
        elif args.command == "list":
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "candidates": [json.loads(encode_research(view)) for view in store.list()],
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "bundle":
            output = args.output.resolve()
            if (
                output.suffix.lower() in (".sqlite3", ".sqlite", ".db")
                or output.name.endswith(("-wal", "-shm"))
                or any(
                    output.is_relative_to(root / folder) for folder in ("archives", "strategies")
                )
            ):
                raise ValueError("Output cannot overwrite research storage")
            dossier = store.bundle(args.candidate_id)
            if dossier is None:
                raise ValueError("Unknown research candidate")
            if output.exists() and output.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Existing output exceeds artifact capacity")
            _write_immutable(output, encode_research(dossier))
            print("Evidence-only bundle exported; promotion_allowed=false")
        else:
            view = store.withdraw(
                args.candidate_id,
                expected_revision=args.expected_revision,
                command_id=args.command_id,
                reason=args.reason,
            )
            print(encode_research(view).decode())
    except ImportError:
        parser.error('Install research prerequisites: python -m pip install -e ".[data,trading]"')
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
