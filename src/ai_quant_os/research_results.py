"""Evidence-only native research results; unavailable gates cannot promote a candidate."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal

from ai_quant_os.registered_backtest import RegisteredBacktest
from ai_quant_os.research_contracts import ResearchPlan
from ai_quant_os.strategy_contracts import StrategyGenome

PENDING_GATES = ("MONTE_CARLO", "ADVERSARIAL", "SANDBOX", "INDEPENDENT_REVIEW", "SHADOW")
MEASURED_GATES = ("WALK_FORWARD_PNL", "HOLDOUT_PNL", "COST_STRESS_PNL", "MINIMUM_ACTIVITY")


def measured_conditions(folds, heldout, heldout_stress):
    with localcontext(Context(prec=28)):
        return (
            (
                "WALK_FORWARD_PNL",
                sum((fold.validation.result.net_pnl for fold in folds), Decimal(0)) >= 0,
            ),
            ("HOLDOUT_PNL", heldout.result.net_pnl >= 0),
            (
                "COST_STRESS_PNL",
                heldout_stress.result.net_pnl >= 0
                and all(fold.stress.result.net_pnl >= 0 for fold in folds),
            ),
            (
                "MINIMUM_ACTIVITY",
                heldout.result.native_order_count > 0
                and all(fold.validation.result.native_order_count > 0 for fold in folds),
            ),
        )


def select_winner(results: tuple[RegisteredBacktest, ...]) -> RegisteredBacktest:
    ordered = sorted(results, key=lambda row: row.binding.genome_sha256)
    return max(ordered, key=lambda row: row.result.net_pnl)


@dataclass(frozen=True)
class ResearchSelection:
    genome: StrategyGenome
    development_fingerprint: str
    training_results: tuple[RegisteredBacktest, ...]

    def __post_init__(self):
        if (
            not self.training_results
            or len(self.training_results) > 16
            or any(
                row.result.dataset_fingerprint != self.development_fingerprint
                for row in self.training_results
            )
            or select_winner(self.training_results).binding.genome != self.genome
        ):
            raise ValueError("Research selection does not match training evidence")


@dataclass(frozen=True)
class FoldEvidence:
    index: int
    training_results: tuple[RegisteredBacktest, ...]
    validation: RegisteredBacktest
    stress: RegisteredBacktest

    def __post_init__(self):
        winner = select_winner(self.training_results).binding.genome
        if (
            self.validation.binding.genome != winner
            or self.stress.binding.genome != winner
            or self.validation.result.dataset_fingerprint != self.stress.result.dataset_fingerprint
        ):
            raise ValueError("Validation candidate differs from training selection")


@dataclass(frozen=True)
class ResearchGate:
    name: str
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str


@dataclass(frozen=True)
class ResearchReport:
    schema_version: Literal["1.0"]
    plan: ResearchPlan
    outcome: Literal["FAILED", "REJECTED", "VALIDATION_INCOMPLETE"]
    promotion_allowed: Literal[False]
    selection: ResearchSelection | None
    folds: tuple[FoldEvidence, ...]
    holdout: RegisteredBacktest | None
    holdout_stress: RegisteredBacktest | None
    gates: tuple[ResearchGate, ...]
    reasons: tuple[str, ...]
    attempts: tuple[RegisteredBacktest, ...] = ()

    def __post_init__(self):
        if self.schema_version != "1.0" or self.promotion_allowed is not False:
            raise ValueError("Research cannot grant promotion authority")
        if len(self.attempts) > 128:
            raise ValueError("Research attempt capacity exceeded")
        if self.outcome == "FAILED":
            if not self.reasons:
                raise ValueError("Failed research requires an archival reason")
            return
        if (
            self.selection is None
            or self.holdout is None
            or self.holdout_stress is None
            or not self.folds
            or self.selection.development_fingerprint != self.plan.development_fingerprint
            or self.holdout.result.dataset_fingerprint != self.plan.holdout_fingerprint
            or self.holdout_stress.result.dataset_fingerprint != self.plan.holdout_fingerprint
            or self.holdout.binding.genome != self.selection.genome
            or self.holdout_stress.binding.genome != self.selection.genome
        ):
            raise ValueError("Research evidence or selected candidate mismatch")
        if len(self.gates) != 9 or {row.name for row in self.gates} != set(
            PENDING_GATES + MEASURED_GATES
        ):
            raise ValueError("Required research gates cannot be skipped")
        if any(row.state != "PENDING" for row in self.gates if row.name in PENDING_GATES):
            raise ValueError("Unimplemented research gates cannot pass")
        measured = dict(measured_conditions(self.folds, self.holdout, self.holdout_stress))
        if any(
            gate.state != ("PASS" if measured[gate.name] else "FAIL")
            for gate in self.gates
            if gate.name in MEASURED_GATES
        ):
            raise ValueError("Measured research gates disagree with native evidence")
        expected = (
            "REJECTED"
            if any(row.state == "FAIL" for row in self.gates)
            else "VALIDATION_INCOMPLETE"
        )
        if self.outcome != expected:
            raise ValueError("Research outcome disagrees with scorecard")
