import hashlib
import sqlite3
from pathlib import Path
from threading import Lock

from ai_quant_os.calibration import CalibrationReport, verify_calibration_report


class CalibrationStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False
        )
        self._lock = Lock()
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS calibration_reports (
                report_id TEXT PRIMARY KEY,
                report_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )""")

    def put(self, report: CalibrationReport) -> bool:
        verify_calibration_report(report)
        raw = report.model_dump_json()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            prior = self._connection.execute(
                "SELECT report_json, content_hash FROM calibration_reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if prior is not None:
                if prior != (raw, digest):
                    raise ValueError("Calibration report integrity conflict")
                return False
            self._connection.execute(
                "INSERT INTO calibration_reports VALUES (?, ?, ?)", (report.report_id, raw, digest)
            )
            return True

    def get(self, report_id: str) -> CalibrationReport | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT report_json, content_hash FROM calibration_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        raw, digest = row
        if hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise ValueError("Calibration report integrity verification failed")
        try:
            report = CalibrationReport.model_validate_json(raw)
            verify_calibration_report(report)
        except ValueError as error:
            raise ValueError("Calibration report integrity verification failed") from error
        if report.report_id != report_id:
            raise ValueError("Calibration report integrity identifier mismatch")
        return report
