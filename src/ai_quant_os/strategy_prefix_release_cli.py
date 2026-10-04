"""Explicit archive preparation or pinned removed-prefix release, without daemon calls."""

import argparse
import json
from pathlib import Path
import sqlite3
import stat

from ai_quant_os.strategy_container_lifecycle import restore_strategy_container_plan
from ai_quant_os.strategy_prefix_jobs import load_prefix_job
from ai_quant_os.strategy_prefix_release import (
    prepare_strategy_prefix_release,
    release_strategy_prefix_inputs,
)
from ai_quant_os.validation_package_store import ValidationPackageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive or release an exact removed prefix job")
    parser.add_argument("--validation-archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job-name", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--index", required=True, type=int)
    for name in ("stage", "create", "ownership", "receipt"):
        prepare.add_argument("--" + name + "-sha256", required=True)
    release = commands.add_parser("release")
    release.add_argument("--release-sha256", required=True)
    args = parser.parse_args()
    try:
        metadata = args.validation_archive.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("Ordinary original package archive required")
        with ValidationPackageStore(args.validation_archive) as store:
            package = store.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package missing")
        if args.command == "prepare":
            job = load_prefix_job(
                package,
                args.index,
                root=args.root,
                job_name=args.job_name,
                stage_sha256=args.stage_sha256,
            )
            plan = restore_strategy_container_plan(
                job,
                package,
                create_sha256=args.create_sha256,
                ownership_sha256=args.ownership_sha256,
            )
            bundle = prepare_strategy_prefix_release(
                plan, job, package, receipt_sha256=args.receipt_sha256
            )
            result = {
                "state": bundle.state,
                "release_sha256": bundle.release_sha256,
                "receipt_sha256": bundle.receipt_sha256,
                "execution_verified": False,
                "promotion_allowed": False,
            }
        else:
            result = release_strategy_prefix_inputs(
                package, root=args.root, job_name=args.job_name, release_sha256=args.release_sha256
            ).model_dump(mode="json")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        parser.exit(
            2,
            "Prefix release refused or partial; retain the pinned archive, attempt record and original package for exact recovery.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
