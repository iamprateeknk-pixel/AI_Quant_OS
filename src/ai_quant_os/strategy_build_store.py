"""Atomic checked build reviews, withdrawals and inert candidate artifacts."""

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.contracts import _require_aware
from ai_quant_os.hypothesis_review_contracts import HypothesisReview, ReviewWithdrawal
from ai_quant_os.strategy_candidate_contracts import CandidateBundle
from ai_quant_os.strategy_builder import render_candidate


class StrategyBuildStore:
    def __init__(self, path: Path | None = None, *, capacity: int = 256):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError("Invalid build archive capacity")
        existed = path is not None and path.exists()
        self._capacity, self._lock = capacity, Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {
                    "hypothesis_reviews",
                    "review_withdrawals",
                    "strategy_candidates",
                    "audit_events",
                    "audit_chain",
                    "audit_head",
                }
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Build archive metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS hypothesis_reviews (review_ref TEXT PRIMARY KEY,review_id TEXT UNIQUE NOT NULL,review_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS review_withdrawals (review_ref TEXT PRIMARY KEY,withdrawal_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS strategy_candidates (candidate_ref TEXT PRIMARY KEY,candidate_json TEXT NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
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
    def _event(action: str, reference: str, raw: str) -> dict:
        return {
            "action": action,
            "reference": reference,
            "artifact_sha256": sha256(raw.encode()).hexdigest(),
            "execution_authorized": False,
        }

    def _verified(self):
        count, largest = self._db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(event_json)),0) FROM audit_events"
        ).fetchone()
        if count > 768 or largest > 4096:
            raise ValueError("Build journal exceeds bound")
        self._audit.require_valid()
        expected = {}
        reviews = {}
        withdrawals = {}
        candidates = {}
        for table, model, bound, target, action in (
            ("hypothesis_reviews", HypothesisReview, 8192, reviews, "HYPOTHESIS_BUILD_REVIEW"),
            (
                "review_withdrawals",
                ReviewWithdrawal,
                4096,
                withdrawals,
                "HYPOTHESIS_BUILD_WITHDRAWAL",
            ),
            ("strategy_candidates", CandidateBundle, 32768, candidates, "INERT_STRATEGY_CANDIDATE"),
        ):
            query = (
                "SELECT review_ref,review_json,review_id FROM hypothesis_reviews"
                if table == "hypothesis_reviews"
                else "SELECT review_ref,withdrawal_json FROM review_withdrawals"
                if table == "review_withdrawals"
                else "SELECT candidate_ref,candidate_json FROM strategy_candidates"
            )
            rows = self._db.execute(query + " LIMIT 257").fetchall()
            if len(rows) > 256:
                raise ValueError("Build archive exceeds bound")
            for reference, raw, *extra in rows:
                if not isinstance(raw, str) or len(raw.encode()) > bound:
                    raise ValueError("Build artifact exceeds byte bound")
                item = model.model_validate_json(raw)
                actual = item.candidate_ref if table == "strategy_candidates" else item.review_ref
                if (
                    reference != actual
                    or item.model_dump_json() != raw
                    or (extra and item.review_id != extra[0])
                ):
                    raise ValueError("Build artifact canonical identity mismatch")
                target[reference] = item
                expected[action + ":" + reference] = self._event(action, reference, raw)
        events = {
            key: json.loads(raw)
            for key, raw in self._db.execute("SELECT event_key,event_json FROM audit_events")
        }
        if events != expected:
            raise ValueError("Build artifact journal binding mismatch")
        for reference, withdrawal in withdrawals.items():
            if reference not in reviews or withdrawal.withdrawn_at < reviews[reference].reviewed_at:
                raise ValueError("Build withdrawal review binding mismatch")
        for artifact in candidates.values():
            if reviews.get(
                artifact.review.review_ref
            ) != artifact.review or artifact != render_candidate(
                review=artifact.review,
                context_ref=artifact.context_ref,
                task_id=artifact.task_id,
                sdk_source_pins=artifact.sdk_source_pins,
            ):
                raise ValueError("Candidate retained recipe mismatch")
        return reviews, withdrawals, candidates

    @staticmethod
    def _active(reviews, withdrawals, reference, now):
        _require_aware(now)
        review = reviews.get(reference)
        if review is None:
            raise KeyError("Hypothesis review not found")
        if reference in withdrawals or not review.reviewed_at <= now < review.valid_until:
            raise PermissionError("Hypothesis build review is unavailable")
        return review

    def active(self, reference: str, *, now: datetime) -> HypothesisReview:
        with self._lock, self._transaction():
            reviews, withdrawals, _ = self._verified()
            return self._active(reviews, withdrawals, reference, now)

    def put_review(self, review: HypothesisReview) -> HypothesisReview:
        review = HypothesisReview.model_validate_json(review.model_dump_json())
        raw = review.model_dump_json()
        if len(raw.encode()) > 8192:
            raise ValueError("Build review exceeds byte bound")
        with self._lock, self._transaction():
            reviews, _, _ = self._verified()
            prior = next(
                (row for row in reviews.values() if row.review_id == review.review_id), None
            )
            if prior is not None:
                if prior != review:
                    raise ValueError("Immutable build review conflict")
                return prior
            if len(reviews) >= self._capacity:
                raise ValueError("Build review capacity exhausted")
            self._db.execute(
                "INSERT INTO hypothesis_reviews VALUES (?,?,?)",
                (review.review_ref, review.review_id, raw),
            )
            self._audit.append(
                "HYPOTHESIS_BUILD_REVIEW:" + review.review_ref,
                self._event("HYPOTHESIS_BUILD_REVIEW", review.review_ref, raw),
            )
            return review

    def withdraw(self, reference: str, *, reason: str, now: datetime) -> ReviewWithdrawal:
        item = ReviewWithdrawal(review_ref=reference, reason=reason, withdrawn_at=now)
        with self._lock, self._transaction():
            reviews, withdrawals, _ = self._verified()
            if reference not in reviews:
                raise KeyError("Hypothesis review not found")
            if now < reviews[reference].reviewed_at:
                raise ValueError("Withdrawal precedes review")
            prior = withdrawals.get(reference)
            if prior:
                if prior.reason != reason:
                    raise ValueError("Immutable withdrawal conflict")
                return prior
            raw = item.model_dump_json()
            self._db.execute("INSERT INTO review_withdrawals VALUES (?,?)", (reference, raw))
            self._audit.append(
                "HYPOTHESIS_BUILD_WITHDRAWAL:" + reference,
                self._event("HYPOTHESIS_BUILD_WITHDRAWAL", reference, raw),
            )
            return item

    def put_candidate(self, candidate: CandidateBundle, *, now: datetime) -> CandidateBundle:
        candidate = CandidateBundle.model_validate_json(candidate.model_dump_json())
        raw = candidate.model_dump_json()
        if len(raw.encode()) > 32768:
            raise ValueError("Candidate artifact exceeds byte bound")
        with self._lock, self._transaction():
            reviews, withdrawals, candidates = self._verified()
            if (
                self._active(reviews, withdrawals, candidate.review.review_ref, now)
                != candidate.review
            ):
                raise ValueError("Candidate approval binding mismatch")
            if candidate != render_candidate(
                review=candidate.review,
                context_ref=candidate.context_ref,
                task_id=candidate.task_id,
                sdk_source_pins=candidate.sdk_source_pins,
            ):
                raise ValueError("Candidate publication differs from declared recipe")
            prior = candidates.get(candidate.candidate_ref)
            if prior is not None:
                return prior
            if len(candidates) >= self._capacity:
                raise ValueError("Candidate archive capacity exhausted")
            self._db.execute(
                "INSERT INTO strategy_candidates VALUES (?,?)", (candidate.candidate_ref, raw)
            )
            self._audit.append(
                "INERT_STRATEGY_CANDIDATE:" + candidate.candidate_ref,
                self._event("INERT_STRATEGY_CANDIDATE", candidate.candidate_ref, raw),
            )
            return candidate

    def get_review(self, reference: str) -> HypothesisReview | None:
        with self._lock, self._transaction():
            return self._verified()[0].get(reference)

    def review_by_id(self, review_id: str) -> HypothesisReview | None:
        with self._lock, self._transaction():
            return next(
                (row for row in self._verified()[0].values() if row.review_id == review_id), None
            )

    def withdrawal(self, reference: str) -> ReviewWithdrawal | None:
        with self._lock, self._transaction():
            return self._verified()[1].get(reference)

    def get_candidate(self, reference: str) -> CandidateBundle | None:
        with self._lock, self._transaction():
            return self._verified()[2].get(reference)

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
