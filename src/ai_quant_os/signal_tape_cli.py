"""Operator-owned native numeric replay; no candidate source or Python test execution."""

import argparse
from decimal import Decimal, DecimalException
from pathlib import Path

from ai_quant_os.signal_tape_contracts import SignalTape
from ai_quant_os.signal_tape_replay import replay_signal_tape
from ai_quant_os.validation_package_store import ValidationPackageStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay unattested JSON targets against a retained validation package"
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--signals", required=True, type=Path)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--initial-balance", type=Decimal, default=Decimal(100000))
    parser.add_argument("--cost-scenario", choices=("BASE", "DECLARED_STRESS"), default="BASE")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if not args.archive.is_file() or (args.output is not None and args.output.exists()):
            raise ValueError("Existing archive and unused output are required")
        with args.signals.open("rb") as source:
            raw = source.read(1048577)
        if not 0 < len(raw) <= 1048576:
            raise ValueError("Signal input exceeds byte bound")
        tape = SignalTape.model_validate_json(raw)
        with ValidationPackageStore(args.archive) as archive:
            package = archive.get(args.package_ref)
        if package is None:
            raise ValueError("Validation package not found")
        report = replay_signal_tape(
            package,
            tape,
            quantity=args.quantity,
            initial_balance=args.initial_balance,
            cost_scenario=args.cost_scenario,
        )
        encoded = report.model_dump_json().encode() + b"\n"
        if len(encoded) > 2097152:
            raise ValueError("Signal replay output exceeds byte bound")
        if args.output is not None:
            with args.output.open("xb") as target:
                target.write(encoded)
        else:
            print(encoded.decode(), end="")
    except (OSError, ValueError, DecimalException):
        parser.exit(
            2,
            "Signal replay refused; check archive, bounded targets, cost model and native runtime.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
