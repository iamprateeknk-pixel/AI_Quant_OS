"""Portable byte-pinned scorecard evaluation and verification; no worker actions."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from ai_quant_os.candidate_dataset_run_contracts import CandidateDatasetRunReport
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_validation import evaluate_candidate, verify_candidate_validation
from ai_quant_os.candidate_validation_contracts import (
    CandidateValidationPolicy,
    CandidateValidationScorecard,
)
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_run_contracts import StrategyPrefixRunReport
from ai_quant_os.validation_package_store import ValidationPackageStore

MAX_METADATA = 134217728
MAX_TOTAL_METADATA = 268435456


def _pinned(pair, model, remaining):
    path, digest = pair
    require_digest(digest, prefix="")
    raw = read_repair_image_input(Path(path), min(MAX_METADATA, remaining))
    if sha256(raw).hexdigest() != digest:
        raise ValueError("Candidate validation input differs from its independent hash")
    checked = model.model_validate_json(raw)
    if raw != checked.model_dump_json().encode():
        raise ValueError("Canonical candidate validation input required")
    return checked, remaining - len(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate or verify pinned candidate scorecards")
    parser.add_argument("--validation-archive", type=Path, required=True)
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--test-result", nargs=2, metavar=("PATH", "SHA256"), required=True)
    parser.add_argument("--holdout-report", nargs=2, metavar=("PATH", "SHA256"), required=True)
    parser.add_argument(
        "--development-report", nargs=2, metavar=("PATH", "SHA256"), action="append", required=True
    )
    parser.add_argument("--policy", nargs=2, metavar=("PATH", "SHA256"))
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--scorecard", nargs=2, metavar=("PATH", "SHA256"), required=True)
    args = parser.parse_args()
    try:
        if not args.validation_archive.is_file() or len(args.development_report) > 2048:
            raise ValueError("Existing validation archive and bounded interval inventory required")
        with ValidationPackageStore(args.validation_archive) as archive:
            package = archive.get(args.package_ref)
        if package is None:
            raise ValueError("Original validation package not found")
        remaining = MAX_TOTAL_METADATA
        tested, remaining = _pinned(args.test_result, CandidateTestResult, remaining)
        holdout, remaining = _pinned(args.holdout_report, StrategyPrefixRunReport, remaining)
        windows = []
        for pair in args.development_report:
            report, remaining = _pinned(pair, CandidateDatasetRunReport, remaining)
            windows.append(report)
        if args.command == "evaluate":
            policy = None
            if args.policy is not None:
                policy, remaining = _pinned(args.policy, CandidateValidationPolicy, remaining)
            scorecard = evaluate_candidate(package, tested, holdout, tuple(windows), policy=policy)
            raw = scorecard.model_dump_json().encode()
            if len(raw) > MAX_METADATA:
                raise ValueError("Scorecard exceeds retained byte capacity")
            with args.output.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            if args.policy is not None:
                raise ValueError("Verification uses the scorecard's original declared policy")
            scorecard, remaining = _pinned(args.scorecard, CandidateValidationScorecard, remaining)
            verify_candidate_validation(package, tested, holdout, tuple(windows), scorecard)
            raw = scorecard.model_dump_json().encode()
        print(
            json.dumps(
                {
                    "scorecard_ref": scorecard.scorecard_ref,
                    "scorecard_sha256": sha256(raw).hexdigest(),
                    "outcome": scorecard.outcome,
                    "verified_binding": True,
                    "isolation_verified": False,
                    "promotion_allowed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        parser.exit(
            2, "Candidate validation refused; retain original inputs and independent hashes.\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
