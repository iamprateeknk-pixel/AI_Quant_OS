"""Isolated counterfactual replays against exact retained forward shadow evidence."""

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from decimal import Context, Inexact, localcontext

from ai_quant_os.counterfactual_contracts import ReplayVariant
from ai_quant_os.counterfactual_results import ReplayRun
from ai_quant_os.market_data import BarDataset
from ai_quant_os.registered_backtest import RegisteredBacktest, run_registered_backtest
from ai_quant_os.research_store import _decode, encode_research
from ai_quant_os.native_bar_evidence import verify_native_bar_result
from ai_quant_os.shadow_portfolio import ShadowReport, verify_forward_source
from ai_quant_os.strategy_registry import prepare_builtin


class ShadowCounterfactualIntegrityError(ValueError):
    """Retained replay bytes or source bindings failed verification."""


class ShadowCounterfactualConflictError(ValueError):
    """A replay identity is already owned by different or unfinished inputs."""


@dataclass(frozen=True)
class ShadowCounterfactualPlan:
    replay_id: str
    shadow_id: str
    sequence: int
    report_sha256: str
    variants: tuple[ReplayVariant, ...]
    schema_version: str = "1.0"
    purpose: str = "FORWARD_SHADOW_COUNTERFACTUAL_DIAGNOSTIC"

    def __post_init__(self):
        if (
            not isinstance(self.replay_id, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.replay_id) is None
            or not isinstance(self.shadow_id, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.shadow_id) is None
            or type(self.sequence) is not int
            or not 1 <= self.sequence <= 1000
            or not isinstance(self.report_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.report_sha256) is None
            or type(self.variants) is not tuple
            or not 1 <= len(self.variants) <= 7
            or any(
                type(row) is not ReplayVariant or row.name.upper() == "BASELINE"
                for row in self.variants
            )
            or len({row.name for row in self.variants}) != len(self.variants)
            or len({row.configuration for row in self.variants}) != len(self.variants)
            or self.schema_version != "1.0"
            or self.purpose != "FORWARD_SHADOW_COUNTERFACTUAL_DIAGNOSTIC"
        ):
            raise ValueError("Invalid forward shadow counterfactual plan")


@dataclass(frozen=True)
class ShadowCounterfactualInputs:
    plan: ShadowCounterfactualPlan
    source_report: ShadowReport

    def __post_init__(self):
        if (
            type(self.plan) is not ShadowCounterfactualPlan
            or type(self.source_report) is not ShadowReport
        ):
            raise ValueError("Immutable plan and retained shadow source report required")
        report = self.source_report
        if (
            report.outcome != "OBSERVED"
            or report.run is None
            or report.assignment.shadow_id != self.plan.shadow_id
            or report.sequence != self.plan.sequence
            or report.fingerprint != self.plan.report_sha256
        ):
            raise ValueError("Forward counterfactual source report identity mismatch")
        source = BarDataset(report.source_bars)
        parent = report.assignment.candidate.report
        holdout = parent.holdout
        baseline = holdout.binding.genome
        parameter_names = {name for name, _ in holdout.binding.implementation.parameter_defaults}
        if (
            source.duplicate_rows
            or source.bars != report.source_bars
            or source.fingerprint != report.run.result.dataset_fingerprint
            or report.run.binding != holdout.binding
            or len(report.source_bars) * (len(self.plan.variants) + 1) > 20000
            or any(
                row.genome.strategy_ref != baseline.strategy_ref
                or {name for name, _ in row.genome.parameters} != parameter_names
                or row.configuration == (baseline, parent.plan.quantity, parent.plan.costs)
                for row in self.plan.variants
            )
        ):
            raise ValueError("Forward counterfactual variants or source are incompatible")


@dataclass(frozen=True)
class ShadowCounterfactualReport:
    inputs: ShadowCounterfactualInputs
    outcome: str
    baseline: RegisteredBacktest | None
    results: tuple[ReplayRun, ...]
    reason: str | None = None
    schema_version: str = "1.0"
    promotion_allowed: bool = False
    causal_attribution_allowed: bool = False

    def __post_init__(self):
        if (
            type(self.inputs) is not ShadowCounterfactualInputs
            or type(self.results) is not tuple
            or self.schema_version != "1.0"
            or self.promotion_allowed is not False
            or self.causal_attribution_allowed is not False
            or self.outcome not in ("COMPLETED", "FAILED")
        ):
            raise ValueError("Invalid forward shadow counterfactual report")
        if self.baseline is None:
            if (
                self.outcome != "FAILED"
                or self.reason
                not in (
                    "SOURCE_VERIFICATION_FAILURE",
                    "NATIVE_EXECUTION_FAILURE",
                )
                or self.results
            ):
                raise ValueError("Missing counterfactual baseline requires a recorded failure")
            return
        source_report = self.inputs.source_report
        verify_forward_source(source_report.assignment, source_report.source_bars)
        verify_native_bar_result(
            self.baseline.result,
            source_report.source_bars,
            source_report.assignment.candidate.report.plan.quantity,
            source_report.assignment.candidate.report.plan.costs,
        )
        baseline_matches = self.baseline == source_report.run
        if self.outcome == "COMPLETED":
            if (
                self.reason is not None
                or not baseline_matches
                or len(self.results) != len(self.inputs.plan.variants)
            ):
                raise ValueError("Completed replay requires exact forward baseline reproduction")
        elif self.reason not in ("BASELINE_REPRODUCIBILITY_FAILURE", "NATIVE_EXECUTION_FAILURE"):
            raise ValueError("Invalid forward counterfactual failure reason")
        elif (self.reason == "BASELINE_REPRODUCIBILITY_FAILURE") == baseline_matches:
            raise ValueError("Forward baseline failure state does not match its source")
        if len(self.results) > len(self.inputs.plan.variants):
            raise ValueError("Unexpected forward counterfactual results")
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            for result, variant in zip(self.results, self.inputs.plan.variants):
                if (
                    type(result) is not ReplayRun
                    or result.variant != variant
                    or result.run.binding.genome != variant.genome
                    or result.run.binding.implementation != self.baseline.binding.implementation
                    or result.run.result.costs != variant.costs
                    or result.run.result.dataset_fingerprint
                    != self.inputs.source_report.run.result.dataset_fingerprint
                    or result.pnl_difference
                    != result.run.result.net_pnl - self.baseline.result.net_pnl
                ):
                    raise ValueError("Forward counterfactual result lineage mismatch")
                verify_native_bar_result(
                    result.run.result,
                    self.inputs.source_report.source_bars,
                    variant.quantity,
                    variant.costs,
                )
            if (
                self.outcome == "COMPLETED"
                and tuple(row.variant for row in self.results) != self.inputs.plan.variants
            ):
                raise ValueError("Forward counterfactual result order mismatch")

    @property
    def source_bars(self):
        return self.inputs.source_report.source_bars


class ShadowCounterfactualStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS shadow_counterfactuals (
                replay_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL,
                inputs BLOB NOT NULL, inputs_sha256 TEXT NOT NULL,
                report BLOB, report_sha256 TEXT
            )""")
            db.commit()

    @staticmethod
    def _verified(row):
        replay_id, token, raw, digest, report_raw, report_digest = row
        try:
            inputs = _decode(raw, digest, ShadowCounterfactualInputs)
            if replay_id != inputs.plan.replay_id or not isinstance(token, str) or len(token) != 32:
                raise ValueError("Forward counterfactual reservation identity mismatch")
            if report_raw is None:
                if report_digest is not None:
                    raise ValueError("Forward counterfactual unfinished archive mismatch")
                return inputs, None
            report = _decode(report_raw, report_digest, ShadowCounterfactualReport)
            if report.inputs != inputs:
                raise ValueError("Forward counterfactual archive inputs mismatch")
            return inputs, report
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            raise ShadowCounterfactualIntegrityError(
                "Forward counterfactual archive integrity verification failed"
            ) from error

    def get(self, replay_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute(
                "SELECT * FROM shadow_counterfactuals WHERE replay_id=?", (replay_id,)
            ).fetchone()
            return None if row is None else self._verified(row)[1]

    def reserve(self, inputs):
        raw = encode_research(inputs)
        if len(raw) > 16 * 1024 * 1024:
            raise ShadowCounterfactualConflictError("Forward counterfactual inputs exceed capacity")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM shadow_counterfactuals WHERE replay_id=?", (inputs.plan.replay_id,)
            ).fetchone()
            if row is not None:
                prior, report = self._verified(row)
                if prior != inputs or row[2] != raw:
                    raise ShadowCounterfactualConflictError(
                        "Immutable forward counterfactual replay cannot change"
                    )
                if report is None:
                    raise ShadowCounterfactualConflictError(
                        "Uncertain forward counterfactual requires investigation"
                    )
                return None, report
            count, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(inputs)+COALESCE(length(report),0)),0) FROM shadow_counterfactuals"
            ).fetchone()
            if count >= 1000 or size + len(raw) > 64 * 1024 * 1024:
                raise ShadowCounterfactualConflictError(
                    "Forward counterfactual archive capacity exceeded"
                )
            token = uuid4().hex
            db.execute(
                "INSERT INTO shadow_counterfactuals VALUES (?,?,?,?,NULL,NULL)",
                (inputs.plan.replay_id, token, raw, sha256(raw).hexdigest()),
            )
            db.commit()
            return token, None

    def finish(self, token, report):
        raw = encode_research(report)
        report = _decode(raw, sha256(raw).hexdigest(), ShadowCounterfactualReport)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM shadow_counterfactuals WHERE replay_id=?",
                (report.inputs.plan.replay_id,),
            ).fetchone()
            if row is None:
                raise ShadowCounterfactualConflictError(
                    "Missing forward counterfactual reservation"
                )
            inputs, existing = self._verified(row)
            if row[1] != token or existing is not None or inputs != report.inputs:
                raise ShadowCounterfactualConflictError(
                    "Forward counterfactual reservation ownership mismatch"
                )
            size = db.execute(
                "SELECT COALESCE(SUM(length(inputs)+COALESCE(length(report),0)),0) FROM shadow_counterfactuals"
            ).fetchone()[0]
            if size + len(raw) > 64 * 1024 * 1024:
                raise ShadowCounterfactualConflictError(
                    "Forward counterfactual archive capacity exceeded"
                )
            db.execute(
                "UPDATE shadow_counterfactuals SET report=?,report_sha256=? WHERE replay_id=?",
                (raw, sha256(raw).hexdigest(), report.inputs.plan.replay_id),
            )
            db.commit()


def run_shadow_counterfactual(inputs, strategies, store):
    token, cached = store.reserve(inputs)
    if cached is not None:
        return cached
    baseline = None
    results = []
    reason = None
    stage = "SOURCE_VERIFICATION_FAILURE"
    try:
        source_report = inputs.source_report
        parent = source_report.assignment.candidate.report
        for variant in inputs.plan.variants:
            _, binding = prepare_builtin(variant.genome, strategies)
            if binding.implementation != parent.holdout.binding.implementation:
                raise ValueError("Forward strategy implementation changed")
        dataset = BarDataset(source_report.source_bars)
        stage = "NATIVE_EXECUTION_FAILURE"
        baseline = run_registered_backtest(
            dataset,
            parent.holdout.binding.genome,
            parent.plan.costs,
            strategies,
            quantity=parent.plan.quantity,
        )
        if baseline != source_report.run:
            reason = "BASELINE_REPRODUCIBILITY_FAILURE"
        else:
            for variant in inputs.plan.variants:
                run = run_registered_backtest(
                    dataset, variant.genome, variant.costs, strategies, quantity=variant.quantity
                )
                results.append(
                    ReplayRun(variant, run, run.result.net_pnl - baseline.result.net_pnl)
                )
    except (ValueError, RuntimeError, OSError, ArithmeticError):
        reason = stage
    report = ShadowCounterfactualReport(
        inputs,
        "COMPLETED" if reason is None else "FAILED",
        baseline,
        tuple(results),
        reason,
    )
    store.finish(token, report)
    return report
