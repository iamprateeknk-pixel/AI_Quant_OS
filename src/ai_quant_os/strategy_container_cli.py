"""Operator prepare/create/owned cleanup; no worker start or strategy execution."""

import argparse
import json
from pathlib import Path
import sqlite3
import stat

from ai_quant_os.strategy_container_lifecycle import (
    prepare_strategy_container,
    restore_strategy_container_plan,
    create_strategy_container,
    cleanup_strategy_container,
)
from ai_quant_os.strategy_prefix_jobs import load_prefix_job
from ai_quant_os.validation_package_store import ValidationPackageStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, create or remove an owned unstarted strategy container"
    )
    parser.add_argument("--validation-archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--stage-sha256", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "create", "cleanup"):
        command = commands.add_parser(name)
        if name != "prepare":
            command.add_argument("--create-sha256", required=True)
            command.add_argument("--ownership-sha256", required=True)
        if name != "cleanup":
            command.add_argument("--image-archive", dest="archive", required=True, type=Path)
            command.add_argument("--base-metadata", required=True, type=Path)
            for field in (
                "base-manifest-digest",
                "worker-sha256",
                "archive-sha256",
                "manifest-digest",
                "config-digest",
            ):
                command.add_argument("--" + field, required=True)
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
            raise ValueError("Ordinary original validation archive required")
        with ValidationPackageStore(args.validation_archive) as store:
            package = store.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package unavailable")
        job = load_prefix_job(
            package,
            args.index,
            root=args.root,
            job_name=args.job_name,
            stage_sha256=args.stage_sha256,
        )
        if args.command != "cleanup":
            image_args = {
                key: getattr(args, key)
                for key in (
                    "archive",
                    "base_metadata",
                    "base_manifest_digest",
                    "worker_sha256",
                    "archive_sha256",
                    "manifest_digest",
                    "config_digest",
                )
            }
        if args.command == "prepare":
            plan = prepare_strategy_container(job, package, **image_args)
            result = {
                "schema_version": "1.0",
                "state": "CREATE_RECIPE_PREPARED",
                "owner": plan.ownership.owner,
                "create_sha256": plan.create_sha256,
                "ownership_sha256": plan.ownership_sha256,
                "execution_verified": False,
                "promotion_allowed": False,
            }
        else:
            plan = restore_strategy_container_plan(
                job,
                package,
                create_sha256=args.create_sha256,
                ownership_sha256=args.ownership_sha256,
            )
            if args.command == "create":
                cid = create_strategy_container(plan, job, package, **image_args)
                result = {
                    "schema_version": "1.0",
                    "state": "CREATED_METADATA_ADMITTED",
                    "owner": plan.ownership.owner,
                    "container_id": cid,
                    "execution_verified": False,
                    "promotion_allowed": False,
                }
            else:
                result = cleanup_strategy_container(plan, job, package).model_dump(mode="json")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        parser.exit(
            2,
            "Strategy container operation refused or uncertain; retain the job and independent plan pins for owned reconciliation.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
