"""Prepare, execute, retrieve or clean one independently pinned source-only test job."""

import argparse
import json
from pathlib import Path
import sqlite3
import stat
import sys

from ai_quant_os.candidate_test_execution import (
    cleanup_candidate_tests,
    prepare_candidate_tests,
    read_candidate_test_result,
    run_candidate_tests,
)
from ai_quant_os.validation_package_store import ValidationPackageStore


def main():
    parser = argparse.ArgumentParser(description="Separate no-data candidate compilation and tests")
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--validation-archive", type=Path, required=True)
    prepare.add_argument("--package-ref", required=True)
    for name in ("run", "result", "cleanup", "checkpoint", "verify-checkpoint"):
        command = commands.add_parser(name)
        command.add_argument("--manifest-sha256", required=True)
        if name == "checkpoint":
            command.add_argument("--output", type=Path, required=True)
        elif name == "verify-checkpoint":
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--checkpoint-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            observed = args.validation_archive.lstat()
            if (
                not stat.S_ISREG(observed.st_mode)
                or stat.S_ISLNK(observed.st_mode)
                or observed.st_nlink != 1
                or getattr(observed, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ):
                raise ValueError("Original ordinary validation archive required")
            with ValidationPackageStore(args.validation_archive) as archive:
                package = archive.get(args.package_ref)
            if package is None:
                raise ValueError("Original validation package unavailable")
            result = {
                "status": "NO_DATA_CANDIDATE_TEST_JOB_PREPARED",
                "manifest_sha256": prepare_candidate_tests(package, args.root),
                "promotion_allowed": False,
            }
        elif args.command in ("checkpoint", "verify-checkpoint"):
            from ai_quant_os.candidate_test_checkpoint_cli import checkpoint_command

            result = checkpoint_command(args)
        elif args.command == "cleanup":
            result = {
                "status": "CANDIDATE_TEST_CONTAINER_REMOVED",
                "container_id": cleanup_candidate_tests(args.root, args.manifest_sha256),
                "promotion_allowed": False,
            }
        else:
            operation = run_candidate_tests if args.command == "run" else read_candidate_test_result
            result = operation(args.root, args.manifest_sha256).model_dump(mode="json")
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        sys.stderr.write(
            "CANDIDATE_TEST_COMMAND_REFUSED: retain the original job and manifest pin\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
