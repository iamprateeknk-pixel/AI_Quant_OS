"""Portable byte-pinned scorecard evaluation and verification; no worker actions."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from ai_quant_os.candidate_dataset_run_contracts import CandidateDatasetRunReport
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_prerequisite_failure import (
    CandidatePrerequisiteFailure,
    record_prerequisite_failure,
    verify_prerequisite_failure,
)
from ai_quant_os.candidate_validation import evaluate_candidate, verify_candidate_validation
from ai_quant_os.candidate_validation_contracts import (
    CandidateValidationPolicy,
    CandidateValidationScorecard,
)
from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_run_contracts import StrategyPrefixRunReport
from ai_quant_os.validation_package_store import ValidationPackageStore
from ai_quant_os.holdout_validator_rpc import (
    HoldoutRequest,
    HoldoutValidationPublication,
    installed_windows_holdout_client,
)

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


def _publish(path, raw):
    if len(raw) > MAX_METADATA:
        raise ValueError("Candidate validation artifact exceeds retained byte capacity")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _failure(args, package, tested, remaining):
    if args.holdout_report or args.development_report or args.policy:
        raise ValueError("Prerequisite failures cannot contain downstream observations or policy")
    if args.command == "archive-failure":
        with CandidateValidationStore(args.record_archive) as archive:
            failure = archive.put_failure(package, tested)
        raw = failure.model_dump_json().encode()
    elif args.command == "record-failure":
        failure = record_prerequisite_failure(package, tested)
        raw = failure.model_dump_json().encode()
        _publish(args.output, raw)
    else:
        failure, _ = _pinned(args.failure, CandidatePrerequisiteFailure, remaining)
        verify_prerequisite_failure(package, tested, failure)
        raw = failure.model_dump_json().encode()
    return {
        "failure_ref": failure.failure_ref,
        "failure_sha256": sha256(raw).hexdigest(),
        "outcome": failure.outcome,
        "verified_binding": True,
        "isolation_verified": False,
        "promotion_allowed": False,
    }


def _publish_service(client, package_ref, publication):
    request = HoldoutRequest(
        request_id=uuid4().hex,
        operation="VALIDATION_PUBLISH",
        agent_id="A10",
        agent_version="0.2.0",
        task_id="operator-validation-publication",
        context_ref="validation-publish:" + uuid4().hex,
        evidence_ref=package_ref,
        publication=publication,
    )
    response = client.call(request)
    expected_prefix = (
        "candidate-prerequisite-failure:"
        if publication.kind == "PREREQUISITE_FAILURE"
        else "candidate-validation:"
    )
    if response.published_ref is None or not response.published_ref.startswith(expected_prefix):
        raise ValueError("Validation service publication reference mismatch")
    if response.aggregate.outcome is None or response.aggregate.gate_count != 15:
        raise ValueError("Validation service publication summary is incomplete")
    reference_key = "failure_ref" if publication.kind == "PREREQUISITE_FAILURE" else "scorecard_ref"
    return {
        reference_key: response.published_ref,
        "summary_sha256": response.summary_sha256,
        "outcome": response.aggregate.outcome,
        "verified_binding": True,
        "isolation_verified": False,
        "promotion_allowed": False,
    }


def main(argv=None, *, holdout_client=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate or verify pinned candidate scorecards")
    parser.add_argument("--validation-archive", type=Path)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--package-ref", required=True)
    parser.add_argument("--test-result", nargs=2, metavar=("PATH", "SHA256"), required=True)
    parser.add_argument("--holdout-report", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument(
        "--development-report", nargs=2, metavar=("PATH", "SHA256"), action="append"
    )
    parser.add_argument("--policy", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--record-archive", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--scorecard", nargs=2, metavar=("PATH", "SHA256"), required=True)
    record_failure = commands.add_parser("record-failure")
    record_failure.add_argument("--output", type=Path, required=True)
    verify_failure = commands.add_parser("verify-failure")
    verify_failure.add_argument("--failure", nargs=2, metavar=("PATH", "SHA256"), required=True)
    commands.add_parser("archive-failure")
    commands.add_parser("archive-evaluation")
    args = parser.parse_args(argv)
    try:
        archiving = args.command in ("archive-failure", "archive-evaluation")
        if args.service:
            if (
                not archiving
                or args.validation_archive is not None
                or args.record_archive is not None
            ):
                raise ValueError("Service publication applies only to archive commands")
            holdout_client = holdout_client or installed_windows_holdout_client()
            package = None
        elif archiving != (args.record_archive is not None):
            raise ValueError("An explicit result archive is required only for archive commands")
        else:
            if args.validation_archive is None or not args.validation_archive.is_file():
                raise ValueError("Existing validation archive required")
            with ValidationPackageStore(args.validation_archive) as archive:
                package = archive.get(args.package_ref)
            if package is None:
                raise ValueError("Original validation package not found")
        if len(args.development_report or ()) > 2048:
            raise ValueError("Bounded development interval inventory required")
        remaining = MAX_TOTAL_METADATA
        tested, remaining = _pinned(args.test_result, CandidateTestResult, remaining)
        if args.command in ("record-failure", "verify-failure", "archive-failure"):
            if args.service:
                if args.holdout_report or args.development_report or args.policy:
                    raise ValueError(
                        "Prerequisite failures cannot contain downstream observations or policy"
                    )
                result = _publish_service(
                    holdout_client,
                    args.package_ref,
                    HoldoutValidationPublication(
                        kind="PREREQUISITE_FAILURE",
                        package_ref=args.package_ref,
                        test_result_json=tested.model_dump_json(),
                    ),
                )
                print(json.dumps(result, sort_keys=True))
                return 0
            print(json.dumps(_failure(args, package, tested, remaining), sort_keys=True))
            return 0
        if not args.holdout_report or not args.development_report:
            raise ValueError(
                "Statistical scorecards require original holdout and development reports"
            )
        holdout, remaining = _pinned(args.holdout_report, StrategyPrefixRunReport, remaining)
        windows = []
        for pair in args.development_report:
            report, remaining = _pinned(pair, CandidateDatasetRunReport, remaining)
            windows.append(report)
        if args.command in ("evaluate", "archive-evaluation"):
            policy = None
            if args.policy is not None:
                policy, remaining = _pinned(args.policy, CandidateValidationPolicy, remaining)
            if args.command == "archive-evaluation":
                if args.service:
                    result = _publish_service(
                        holdout_client,
                        args.package_ref,
                        HoldoutValidationPublication(
                            kind="EVALUATION",
                            package_ref=args.package_ref,
                            test_result_json=tested.model_dump_json(),
                            holdout_report_json=holdout.model_dump_json(),
                            development_reports_json=tuple(
                                row.model_dump_json() for row in windows
                            ),
                            policy_json=None if policy is None else policy.model_dump_json(),
                        ),
                    )
                    print(json.dumps(result, sort_keys=True))
                    return 0
                with CandidateValidationStore(args.record_archive) as archive:
                    scorecard = archive.put_evaluation(
                        package, tested, holdout, tuple(windows), policy=policy
                    )
            else:
                scorecard = evaluate_candidate(
                    package, tested, holdout, tuple(windows), policy=policy
                )
            raw = scorecard.model_dump_json().encode()
            if args.command == "evaluate":
                _publish(args.output, raw)
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
