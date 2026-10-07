"""Explicit prepared full-prefix runs and independently pinned offline reconciliation."""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import stat

from ai_quant_os.strategy_prefix_run import (
    prepare_strategy_prefix_run,
    run_strategy_prefixes,
    recover_strategy_prefix_run,
    recover_strategy_prefix_run_report,
)
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.candidate_dataset_views import development_window, CandidateDatasetView
from ai_quant_os.candidate_test_execution import read_candidate_test_result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, run or reconcile a pinned full-prefix run"
    )
    parser.add_argument("--validation-archive", required=True, type=Path)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--development-fold", type=int)
    parser.add_argument("--candidate-test-root", type=Path)
    parser.add_argument("--candidate-test-manifest-sha256")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--image-archive", dest="archive", required=True, type=Path)
    prepare.add_argument("--base-metadata", required=True, type=Path)
    for name in (
        "base-manifest-digest",
        "worker-sha256",
        "archive-sha256",
        "manifest-digest",
        "config-digest",
    ):
        prepare.add_argument("--" + name, required=True)
    prepare.add_argument("--quantity", type=int, default=1)
    prepare.add_argument("--initial-balance", type=Decimal, default=Decimal("100000"))
    for name in ("run", "recover-prefix", "recover-report"):
        command = commands.add_parser(name)
        command.add_argument("--run-sha256", required=True)
        if name == "recover-prefix":
            command.add_argument("--release-sha256", required=True)
        elif name == "recover-report":
            command.add_argument("--report-sha256", required=True)
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
        with ValidationPackageStore(args.validation_archive) as archive:
            package = archive.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package not found")
        if args.development_fold is None:
            if (
                args.candidate_test_root is not None
                or args.candidate_test_manifest_sha256 is not None
            ):
                raise ValueError("Test evidence options require an explicit development fold")
        else:
            if args.candidate_test_root is None or args.candidate_test_manifest_sha256 is None:
                raise ValueError("Development folds require independently pinned test job evidence")
            tested = read_candidate_test_result(
                args.candidate_test_root, args.candidate_test_manifest_sha256
            )
            package = development_window(package, args.development_fold, tested)
        if args.command == "prepare":
            fields = (
                "archive",
                "base_metadata",
                "base_manifest_digest",
                "worker_sha256",
                "archive_sha256",
                "manifest_digest",
                "config_digest",
            )
            manifest = prepare_strategy_prefix_run(
                package,
                root=args.root,
                quantity=args.quantity,
                initial_balance=args.initial_balance,
                **{key: getattr(args, key) for key in fields},
            )
            result = {
                "state": manifest.state,
                "run_sha256": manifest.run_sha256,
                "prefix_count": manifest.row_count - 2,
                "execution_verified": False,
                "promotion_allowed": False,
            }
        elif args.command == "recover-prefix":
            row = recover_strategy_prefix_run(
                package,
                root=args.root,
                run_sha256=args.run_sha256,
                release_sha256=args.release_sha256,
            )
            result = {
                "state": "PREFIX_RECOVERED_UNATTESTED",
                "index": row.index,
                "job_name": row.job_name,
                "execution_verified": False,
                "promotion_allowed": False,
            }
        else:
            if args.command == "run":
                report = run_strategy_prefixes(package, root=args.root, run_sha256=args.run_sha256)
            else:
                report = recover_strategy_prefix_run_report(
                    package,
                    root=args.root,
                    run_sha256=args.run_sha256,
                    report_sha256=args.report_sha256,
                )
            result = {
                "status": report.status,
                "report_ref": report.report_ref,
                "report_sha256": sha256(report.model_dump_json().encode()).hexdigest(),
                "execution_verified": False,
                "promotion_allowed": False,
            }
        if type(package) is CandidateDatasetView:
            result["dataset_window_ref"] = package.window.window_ref
            result["selection_data_overlap"] = True
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        parser.exit(
            2,
            "Full prefix run refused, uncertain or partial; retain original package, run pins and owned job/recovery records for explicit reconciliation.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
