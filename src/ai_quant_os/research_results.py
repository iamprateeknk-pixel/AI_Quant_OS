"""Evidence-only native research results; unavailable gates cannot promote a candidate."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from itertools import combinations
from typing import Literal

from ai_quant_os.registered_backtest import RegisteredBacktest
from ai_quant_os.research_contracts import ResearchPlan
from ai_quant_os.strategy_contracts import StrategyGenome
from ai_quant_os.research_monte_carlo import NativeMonteCarlo, evaluate_native_monte_carlo
from ai_quant_os.research_adversarial import NativeAdversarial, verify_adversarial

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


def selection_diagnostic(folds):
    with localcontext(Context(prec=28)):
        return _selection_diagnostic(folds)


def _selection_diagnostic(folds):
    ranked = tuple(
        sorted(
            fold.validation_candidates,
            key=lambda row: (-row.result.net_pnl, row.binding.genome_sha256),
        )
        for fold in folds
    )
    ranks = tuple(
        next(
            index
            for index, row in enumerate(rows, 1)
            if row.binding.genome_sha256 == fold.validation.binding.genome_sha256
        )
        for fold, rows in zip(folds, ranked, strict=True)
    )
    candidate_count = len(folds[0].validation_candidates)
    below_median = sum(rank > (candidate_count + 1) // 2 for rank in ranks)
    if len(folds) < 4 or len(folds) % 2:
        raise ValueError(
            "Selection diagnostic requires an even set of at least four validation folds"
        )
    candidate_ids = tuple(
        sorted(row.binding.genome_sha256 for row in folds[0].validation_candidates)
    )
    split_count = below_median_count = 0
    half = len(folds) // 2
    for in_sample in combinations(range(len(folds)), half):
        if 0 not in in_sample:
            continue
        out_sample = tuple(index for index in range(len(folds)) if index not in in_sample)
        in_scores = {
            candidate_id: sum(
                (
                    next(
                        row.result.net_pnl
                        for row in folds[index].validation_candidates
                        if row.binding.genome_sha256 == candidate_id
                    )
                    for index in in_sample
                ),
                Decimal(0),
            )
            for candidate_id in candidate_ids
        }
        selected = min(
            candidate_ids, key=lambda candidate_id: (-in_scores[candidate_id], candidate_id)
        )
        out_scores = {
            candidate_id: sum(
                (
                    next(
                        row.result.net_pnl
                        for row in folds[index].validation_candidates
                        if row.binding.genome_sha256 == candidate_id
                    )
                    for index in out_sample
                ),
                Decimal(0),
            )
            for candidate_id in candidate_ids
        }
        out_ranking = sorted(
            candidate_ids, key=lambda candidate_id: (-out_scores[candidate_id], candidate_id)
        )
        selected_rank = out_ranking.index(selected) + 1
        split_count += 1
        below_median_count += selected_rank > (candidate_count + 1) // 2
    return SelectionDiagnostic(
        "CSCV_EQUAL_BLOCK_NET_PNL_RANK_V1",
        candidate_count,
        ranks,
        below_median,
        Decimal(below_median) / Decimal(len(ranks)),
        split_count,
        below_median_count,
        Decimal(below_median_count) / Decimal(split_count),
    )


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
    validation_candidates: tuple[RegisteredBacktest, ...] = ()

    def __post_init__(self):
        winner = select_winner(self.training_results).binding.genome
        if (
            self.validation.binding.genome != winner
            or self.stress.binding.genome != winner
            or self.validation.result.dataset_fingerprint != self.stress.result.dataset_fingerprint
        ):
            raise ValueError("Validation candidate differs from training selection")
        if self.validation_candidates:
            if (
                len(self.validation_candidates) != len(self.training_results)
                or len({row.binding.genome_sha256 for row in self.validation_candidates})
                != len(self.validation_candidates)
                or any(
                    row.result.dataset_fingerprint != self.validation.result.dataset_fingerprint
                    or row.result.costs != self.validation.result.costs
                    for row in self.validation_candidates
                )
                or not any(row == self.validation for row in self.validation_candidates)
            ):
                raise ValueError("Candidate validation matrix is incomplete or unbound")


@dataclass(frozen=True)
class SelectionDiagnostic:
    method: Literal["CSCV_EQUAL_BLOCK_NET_PNL_RANK_V1"]
    candidate_count: int
    selected_winner_ranks: tuple[int, ...]
    below_median_count: int
    below_median_fraction: Decimal
    cscv_split_count: int
    cscv_below_median_count: int
    cscv_probability: Decimal


@dataclass(frozen=True)
class ResearchGate:
    name: str
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str


@dataclass(frozen=True)
class ResearchReport:
    schema_version: Literal["1.0", "1.1", "1.2", "1.3"]
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
    monte_carlo: NativeMonteCarlo | None = None
    adversarial: NativeAdversarial | None = None
    selection_diagnostic: SelectionDiagnostic | None = None

    def __post_init__(self):
        if (
            self.schema_version not in ("1.0", "1.1", "1.2", "1.3")
            or self.promotion_allowed is not False
        ):
            raise ValueError("Research cannot grant promotion authority")
        if self.schema_version != self.plan.schema_version:
            raise ValueError("Research report version differs from plan")
        if self.schema_version == "1.0" and self.monte_carlo is not None:
            raise ValueError("Version 1.0 cannot claim Monte Carlo evidence")
        if self.schema_version not in ("1.2", "1.3") and self.adversarial is not None:
            raise ValueError("Legacy research cannot claim adversarial evidence")
        if (self.schema_version == "1.3" and self.outcome != "FAILED") != (
            self.selection_diagnostic is not None
        ):
            raise ValueError("Research 1.3 requires its selection diagnostic")
        if len(self.attempts) > 128:
            raise ValueError("Research attempt capacity exceeded")
        if self.outcome == "FAILED":
            if not self.reasons:
                raise ValueError("Failed research requires an archival reason")
            if self.monte_carlo is not None:
                raise ValueError("Failed research cannot claim unbound Monte Carlo evidence")
            if self.adversarial is not None:
                raise ValueError("Failed research cannot claim adversarial evidence")
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
        pending = (
            PENDING_GATES
            if self.schema_version == "1.0"
            else tuple(
                name
                for name in PENDING_GATES
                if name != "MONTE_CARLO"
                and (name != "ADVERSARIAL" or self.schema_version not in ("1.2", "1.3"))
            )
        )
        if any(row.state != "PENDING" for row in self.gates if row.name in pending):
            raise ValueError("Unimplemented research gates cannot pass")
        if self.schema_version in ("1.1", "1.2", "1.3"):
            expected_mc = evaluate_native_monte_carlo(self.holdout, self.plan.monte_carlo_spec)
            if self.monte_carlo != expected_mc:
                raise ValueError("Monte Carlo result differs from declared native evidence")
            gate = next(row for row in self.gates if row.name == "MONTE_CARLO")
            if gate.state != expected_mc.result.state or gate.reason != expected_mc.result.reason:
                raise ValueError("Monte Carlo gate differs from measured result")
        if self.schema_version in ("1.2", "1.3"):
            verify_adversarial(self.adversarial, self.plan, self.holdout, self.attempts)
            gate = next(row for row in self.gates if row.name == "ADVERSARIAL")
            if (gate.state, gate.reason) != (self.adversarial.state, self.adversarial.reason):
                raise ValueError("Adversarial gate differs from measured result")
        if self.schema_version == "1.3":
            if len(self.folds) < 4 or len(self.folds) % 2:
                raise ValueError(
                    "Research 1.3 requires an even set of at least four validation folds"
                )
            candidates = {row.fingerprint for row in self.plan.candidates}
            if any(
                {row.binding.genome_sha256 for row in fold.validation_candidates} != candidates
                for fold in self.folds
            ):
                raise ValueError("Selection diagnostic candidate set differs from declared plan")
            if self.selection_diagnostic != selection_diagnostic(self.folds):
                raise ValueError("Selection diagnostic differs from native validation evidence")
        elif any(fold.validation_candidates for fold in self.folds):
            raise ValueError("Legacy research cannot include retrospective candidate trials")
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
