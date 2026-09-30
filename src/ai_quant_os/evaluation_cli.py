"""Build or verify an evidence-only classifier evaluation artifact."""

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ai_quant_os.calibration import (
    CalibrationReport,
    EvaluationBatch,
    build_calibration_report,
    verify_calibration_report,
)
from ai_quant_os.calibration_store import CalibrationStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classifier evaluation evidence; no trading authority"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report_command = commands.add_parser("report")
    report_command.add_argument("input", type=Path)
    report_command.add_argument("--output", type=Path, required=True)
    report_command.add_argument("--database", type=Path)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        if args.input.stat().st_size > 20_000_000:
            raise ValueError("Evaluation input exceeds 20 MB")
        raw = args.input.read_bytes()
        if args.command == "verify":
            report = CalibrationReport.model_validate_json(raw)
            verify_calibration_report(report)
            print(f"Verified {report.report_id}: EVIDENCE_ONLY")
            return
        batch = EvaluationBatch.model_validate_json(raw)
        report = build_calibration_report(
            batch.records,
            provider_id=batch.provider_id,
            provider_version=batch.provider_version,
            dataset_ref=batch.dataset_ref,
            created_at=datetime.now(timezone.utc),
        )
        # Exclusive creation preserves an existing report artifact.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(report.model_dump_json(indent=2) + "\n")
        if args.database is not None:
            CalibrationStore(args.database).put(report)
        print(f"Saved {report.report_id}: {report.attempt_count} attempts, EVIDENCE_ONLY")
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(2, "Evaluation failed: input/output validation or storage error\n")


if __name__ == "__main__":
    main()
