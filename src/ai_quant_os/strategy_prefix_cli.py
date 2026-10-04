"""Operator staging/inspection/discard of one inert request from a checked archive."""

import argparse
import json
from pathlib import Path
import sqlite3
import stat

from ai_quant_os.strategy_prefix_jobs import load_prefix_job, stage_prefix_job
from ai_quant_os.validation_package_store import ValidationPackageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage one prefix request; execute no source")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--root", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stage")
    for name in ("inspect", "discard"):
        command = commands.add_parser(name)
        command.add_argument("--job-name", required=True)
        command.add_argument("--stage-sha256", required=True)
    args = parser.parse_args()
    try:
        metadata = args.archive.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("An ordinary existing validation archive is required")
        with ValidationPackageStore(args.archive) as archive:
            package = archive.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package unavailable")
        if args.command == "stage":
            job = stage_prefix_job(package, args.index, root=args.root)
            state = "PREFIX_STAGED"
        else:
            job = load_prefix_job(
                package,
                args.index,
                root=args.root,
                job_name=args.job_name,
                stage_sha256=args.stage_sha256,
            )
            state = "PREFIX_STAGED_INSPECTED"
            if args.command == "discard":
                job.discard_unstarted(package)
                state = "UNSTARTED_PREFIX_DISCARDED"
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "state": state,
                    "job_name": job.path.name,
                    "invocation_ref": job.invocation_ref,
                    "stage_sha256": job.stage_sha256,
                    "execution_verified": False,
                    "promotion_allowed": False,
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(
            2,
            "Prefix staging refused; retain uncertain inputs and check original archive, independent stage pin and owned paths.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
