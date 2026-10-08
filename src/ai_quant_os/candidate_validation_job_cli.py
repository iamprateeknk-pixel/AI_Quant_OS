"""Explicit operator dispatch of one independently pinned validation stage."""

import argparse
import asyncio
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import stat
import sys
import re

from ai_quant_os.candidate_validation_contracts import CandidateValidationPolicy
from ai_quant_os.candidate_validation_job import (
    prepare_validation_job,
    recover_validation_job,
    step_validation_job,
    validation_job_status,
)
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.validation_package_store import ValidationPackageStore


def _loopback_endpoint(value):
    found = re.fullmatch(r"(?:127\.0\.0\.1|localhost):([0-9]{1,5})", value)
    if found is None or not 1 <= int(found[1]) <= 65535:
        raise argparse.ArgumentTypeError("A literal loopback Temporal endpoint is required")
    return "127.0.0.1:" + str(int(found[1]))


async def _workflow(package, args):
    from temporalio.client import Client
    from temporalio.exceptions import TemporalError
    from temporalio.worker import Worker
    from ai_quant_os.temporal_validation import (
        CandidateValidationWorkflow,
        ValidationActivities,
        start_validation_workflow,
    )

    try:
        activities = await asyncio.to_thread(
            ValidationActivities,
            package,
            root=args.root,
            job_sha256=args.job_sha256,
            archive_path=args.record_archive,
        )
        client = await asyncio.wait_for(Client.connect(args.temporal_endpoint), timeout=10)
        queue = "validation-" + args.job_sha256[:16] + "-" + args.run_id
        async with Worker(
            client,
            task_queue=queue,
            workflows=[CandidateValidationWorkflow],
            activities=activities.registered(),
            max_concurrent_activities=1,
            graceful_shutdown_timeout=timedelta(0),
        ):
            handle = await start_validation_workflow(
                client, activities, run_id=args.run_id, task_queue=queue
            )
            return await handle.result()
    except TemporalError as error:
        raise RuntimeError(
            "Local validation workflow refused; retain its history and stage custody"
        ) from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Audited serial original-source validation stages")
    parser.add_argument("--validation-archive", type=Path, required=True)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--image-archive", dest="archive", type=Path, required=True)
    prepare.add_argument("--base-metadata", type=Path, required=True)
    image_fields = (
        "base_manifest_digest",
        "worker_sha256",
        "archive_sha256",
        "manifest_digest",
        "config_digest",
    )
    for name in image_fields:
        prepare.add_argument("--" + name.replace("_", "-"), required=True)
    prepare.add_argument("--quantity", type=int, default=1)
    prepare.add_argument("--initial-balance", type=Decimal, default=Decimal("100000"))
    prepare.add_argument("--policy", nargs=2, metavar=("JSON", "SHA256"))
    for name in ("status", "step", "recover", "workflow", "interruption", "verify-interruption"):
        command = commands.add_parser(name)
        command.add_argument("--job-sha256", required=True)
        if name in ("step", "recover", "workflow"):
            command.add_argument("--record-archive", type=Path, required=True)
        if name == "recover":
            command.add_argument("--result-sha256", required=True)
            command.add_argument("--stage-manifest-sha256")
        elif name == "workflow":
            command.add_argument("--run-id", required=True)
            command.add_argument(
                "--temporal-endpoint", type=_loopback_endpoint, default="127.0.0.1:7233"
            )
        elif name == "interruption":
            command.add_argument("--output", type=Path, required=True)
        elif name == "verify-interruption":
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--interruption-sha256", required=True)
    args = parser.parse_args()
    try:
        observed = args.validation_archive.lstat()
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_size > 134217728
            or getattr(observed, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("Original ordinary package archive required")
        with ValidationPackageStore(args.validation_archive) as packages:
            package = packages.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package not found")
        if args.command == "prepare":
            policy = None
            if args.policy:
                path, pin = args.policy
                require_digest(pin, prefix="")
                raw = read_repair_image_input(Path(path), 65536)
                if sha256(raw).hexdigest() != pin:
                    raise ValueError("Policy differs from independent byte pin")
                policy = CandidateValidationPolicy.model_validate_json(raw)
                if raw != policy.model_dump_json().encode():
                    raise ValueError("Canonical original policy required")
            manifest = prepare_validation_job(
                package,
                root=args.root,
                policy=policy,
                quantity=args.quantity,
                initial_balance=args.initial_balance,
                archive=args.archive,
                base_metadata=args.base_metadata,
                **{key: getattr(args, key) for key in image_fields},
            )
            result = {
                "job_sha256": manifest.job_sha256,
                "stage_count": manifest.stage_count,
                "stage": None,
                "promotion_allowed": False,
            }
        elif args.command == "workflow":
            result = asyncio.run(_workflow(package, args))
        elif args.command in ("interruption", "verify-interruption"):
            from ai_quant_os.validation_interruption_cli import interruption_command

            result = interruption_command(package, args)
        else:
            row = validation_job_status(package, root=args.root, job_sha256=args.job_sha256)
            if args.command != "status":
                with CandidateValidationStore(args.record_archive) as archive:
                    if args.command == "step":
                        row = step_validation_job(
                            package, root=args.root, job_sha256=args.job_sha256, archive=archive
                        )
                    else:
                        row = recover_validation_job(
                            package,
                            root=args.root,
                            job_sha256=args.job_sha256,
                            result_sha256=args.result_sha256,
                            stage_manifest_sha256=args.stage_manifest_sha256,
                            archive=archive,
                        )
            result = {
                "job_sha256": args.job_sha256,
                "stage": row.model_dump(mode="json") if row else None,
                "promotion_allowed": False,
            }
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        return 0
    except (ValueError, OSError, RuntimeError, ImportError, sqlite3.Error):
        sys.stderr.write(
            "VALIDATION_JOB_REFUSED: retain original stage custody and independent pins\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
