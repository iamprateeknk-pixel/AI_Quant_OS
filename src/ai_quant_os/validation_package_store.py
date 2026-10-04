"""Bounded immutable SQLite archive of validation inputs with atomic byte-bound audit."""

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.validation_package_contracts import ValidationPackage


class ValidationPackageStore:
    def __init__(self, path: Path | None = None, *, capacity: int = 32) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("Invalid validation package capacity")
        existed = path is not None and path.exists()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"validation_packages", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Validation package metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS validation_packages (package_ref TEXT PRIMARY KEY, request_ref TEXT UNIQUE NOT NULL, package_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY, event_json TEXT NOT NULL)"
                )
                self._audit.initialize()
                self._verified()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _event(package: ValidationPackage) -> dict:
        return {
            "action": "INERT_VALIDATION_INPUTS_RETAINED",
            "package_ref": package.package_ref,
            "request_ref": package.request_ref,
            "package_sha256": sha256(package.model_dump_json().encode()).hexdigest(),
        }

    def _verified(self) -> tuple[ValidationPackage, ...]:
        count, total, maximum = self._db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(package_json AS BLOB))),0),COALESCE(MAX(LENGTH(CAST(package_json AS BLOB))),0) FROM validation_packages"
        ).fetchone()
        events, event_bytes = self._db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if (
            count > 32
            or total > 33554432
            or maximum > 4194304
            or events != count
            or event_bytes > 4096
        ):
            raise ValueError("Validation package capacity/audit mismatch")
        integrity = self._audit.require_valid()
        if integrity.event_count != count or integrity.legacy_event_count:
            raise ValueError("Validation package audit binding mismatch")
        packages, plans = [], {}
        for reference, request, encoded in self._db.execute(
            "SELECT package_ref,request_ref,package_json FROM validation_packages ORDER BY rowid"
        ):
            package = ValidationPackage.model_validate_json(encoded)
            if (package.package_ref, package.request_ref, package.model_dump_json()) != (
                reference,
                request,
                encoded,
            ):
                raise ValueError("Validation package identity mismatch")
            event = self._db.execute(
                "SELECT event_json FROM audit_events WHERE event_key=?", (reference,)
            ).fetchone()
            if event is None or json.loads(event[0]) != self._event(package):
                raise ValueError("Validation package event binding mismatch")
            if package.plan.plan_id in plans and plans[package.plan.plan_id] != package.plan:
                raise ValueError("Immutable validation plan identity conflict")
            plans[package.plan.plan_id] = package.plan
            packages.append(package)
        return tuple(packages)

    def put(self, package: ValidationPackage) -> ValidationPackage:
        encoded = package.model_dump_json()
        if len(encoded.encode()) > 4194304:
            raise ValueError("Validation package byte bound exceeded")
        package = ValidationPackage.model_validate_json(encoded)
        with self._lock, self._transaction():
            rows = self._verified()
            for prior in rows:
                if prior.plan.plan_id == package.plan.plan_id and prior.plan != package.plan:
                    raise ValueError("Immutable validation plan identity conflict")
                if prior.request_ref == package.request_ref:
                    if prior.model_dump(exclude={"captured_at"}) != package.model_dump(
                        exclude={"captured_at"}
                    ):
                        raise ValueError("Immutable validation request artifact conflict")
                    return prior
            if (
                len(rows) >= self._capacity
                or sum(len(row.model_dump_json().encode()) for row in rows) + len(encoded.encode())
                > 33554432
            ):
                raise ValueError("Validation package capacity exhausted")
            self._db.execute(
                "INSERT INTO validation_packages VALUES (?,?,?)",
                (package.package_ref, package.request_ref, encoded),
            )
            self._audit.append(package.package_ref, self._event(package))
            return package

    def get(self, reference: str) -> ValidationPackage | None:
        with self._lock, self._transaction():
            return next((row for row in self._verified() if row.package_ref == reference), None)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "ValidationPackageStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
