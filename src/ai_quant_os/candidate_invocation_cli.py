"""Export one inert prefix request or assemble untrusted numeric JSON responses."""

import argparse
import json
from pathlib import Path
import sqlite3

from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.candidate_invocations import prepare_invocation, assemble_unattested_tape
from ai_quant_os.validation_package_store import ValidationPackageStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare prefix-only candidate input; execute no source"
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--index", required=True, type=int)
    prepare.add_argument("--output", required=True, type=Path)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--response", required=True, action="append", type=Path)
    assemble.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if not args.archive.is_file() or args.output.exists():
            raise ValueError("Existing archive and unused output are required")
        with ValidationPackageStore(args.archive) as archive:
            package = archive.get(args.package_ref)
        if package is None:
            raise ValueError("Validation package not found")
        if args.command == "prepare":
            value = prepare_invocation(package, args.index)
            summary = {
                "status": "WAIT_FOR_CONFINED_PREFIX_WORKER",
                "invocation_ref": value.invocation_ref,
                "index": value.index,
            }
            maximum = 524288
        else:
            if len(args.response) > 4094:
                raise ValueError("Too many target responses")
            outputs = []
            for path in args.response:
                with path.open("rb") as source:
                    outputs.append(CandidateTargetOutput.from_wire(source.read(2049)))
            value = assemble_unattested_tape(package, outputs)
            summary = {"status": "UNATTESTED", "tape_ref": value.tape_ref}
            maximum = 1048576
        raw = value.model_dump_json().encode()
        if len(raw) > maximum:
            raise ValueError("Output exceeds byte bound")
        with args.output.open("xb") as destination:
            destination.write(raw)
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(
            2,
            "Candidate input refused; check existing archive, prefix index, bounded responses and unused output.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
