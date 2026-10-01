"""Source-retaining counterfactual evidence with reconciled native cash flows."""

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from ai_quant_os.counterfactual_contracts import CounterfactualPlan, ReplayVariant
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.native_bar_evidence import verify_native_bar_result
from ai_quant_os.registered_backtest import RegisteredBacktest
from ai_quant_os.research_candidates import CandidateBundle


@dataclass(frozen=True)
class ReplayInputs:
    plan: CounterfactualPlan
    candidate: CandidateBundle
    source_bars: tuple[Bar, ...]

    def __post_init__(self):
        if type(self.plan) is not CounterfactualPlan or type(self.candidate) is not CandidateBundle:
            raise ValueError("A frozen candidate and declared replay plan are required")
        if type(self.source_bars) is not tuple or not 2 <= len(self.source_bars) <= 4096:
            raise ValueError("A bounded immutable source snapshot is required")
        if any(type(row) is not Bar for row in self.source_bars):
            raise ValueError("Invalid counterfactual source")
        with localcontext(Context(prec=28)):
            source = BarDataset(self.source_bars)
            parent = self.candidate.report
            manifest = self.candidate.manifest
            if (
                source.bars != self.source_bars
                or source.duplicate_rows
                or source.fingerprint != self.plan.dataset_fingerprint
                or self.plan.dataset_fingerprint != parent.plan.holdout_fingerprint
                or self.plan.candidate_id != manifest.candidate_id
                or self.plan.candidate_report_sha256 != manifest.report_sha256
                or parent.holdout is None
                or parent.selection is None
                or {bar.instrument_id for bar in source.bars}
                != {parent.plan.holdout_scope.instrument_id}
                or source.bars[0].observed_at != parent.plan.holdout_scope.first_observed_at
                or source.bars[-1].observed_at != parent.plan.holdout_scope.last_observed_at
            ):
                raise ValueError("Counterfactual source or candidate binding mismatch")
        if len(source.bars) * (len(self.plan.variants) + 1) > 20000:
            raise ValueError("Counterfactual execution budget exceeded")
        baseline = self.baseline
        names = {name for name, _ in parent.holdout.binding.implementation.parameter_defaults}
        for row in self.plan.variants:
            if (
                row.genome.strategy_ref != baseline.genome.strategy_ref
                or {name for name, _ in row.genome.parameters} != names
                or row.configuration == baseline.configuration
            ):
                raise ValueError(
                    "Alternatives must change the frozen baseline configuration explicitly"
                )

    @property
    def baseline(self):
        parent = self.candidate.report
        return ReplayVariant(
            "BASELINE", parent.holdout.binding.genome, parent.plan.quantity, parent.plan.costs
        )

    @property
    def variants(self):
        return (self.baseline, *self.plan.variants)


@dataclass(frozen=True)
class ReplayRun:
    variant: ReplayVariant
    run: RegisteredBacktest
    pnl_difference: Decimal


def _verify_run(row, inputs):
    result = row.run.result
    baseline = inputs.candidate.report.holdout
    if (
        row.run.binding.genome != row.variant.genome
        or row.run.binding.implementation != baseline.binding.implementation
        or result.dataset_fingerprint != inputs.plan.dataset_fingerprint
        or result.costs != row.variant.costs
        or result.strategy_ref != row.variant.genome.strategy_ref
        or result.instrument_id != baseline.result.instrument_id
        or result.pnl_currency != baseline.result.pnl_currency
        or result.engine != "NAUTILUS_BACKTEST"
        or result.engine_version != "1.221.0"
        or result.venue != "PAPER"
        or result.replay_model != "BAR_OPEN_CLOSE_QUOTES_V1"
        or result.pnl_method != "EXACT_FILL_CASHFLOWS_PLUS_FINAL_CLOSE_MARK"
        or row.pnl_difference != result.net_pnl - baseline.result.net_pnl
    ):
        raise ValueError("Counterfactual native evidence binding mismatch")
    verify_native_bar_result(result, inputs.source_bars, row.variant.quantity, row.variant.costs)


@dataclass(frozen=True)
class CounterfactualReport:
    inputs: ReplayInputs
    outcome: Literal["COMPLETED", "FAILED"]
    results: tuple[ReplayRun, ...]
    reason: str | None = None
    schema_version: Literal["1.0"] = "1.0"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        if self.schema_version != "1.0" or self.promotion_allowed is not False:
            raise ValueError("Counterfactual evidence has no promotion authority")
        if type(self.inputs) is not ReplayInputs or type(self.results) is not tuple:
            raise ValueError("Immutable counterfactual evidence required")
        expected = self.inputs.variants
        if len(self.results) > len(expected):
            raise ValueError("Unexpected counterfactual results")
        context = Context(prec=28)
        context.traps[Inexact] = True
        with localcontext(context):
            for row, variant in zip(self.results, expected):
                if type(row) is not ReplayRun or row.variant != variant:
                    raise ValueError("Counterfactual result order differs from declaration")
                _verify_run(row, self.inputs)
        if self.outcome == "COMPLETED":
            if (
                self.reason is not None
                or len(self.results) != len(expected)
                or self.results[0].run != self.inputs.candidate.report.holdout
            ):
                raise ValueError(
                    "Completed diagnostics require a reproduced baseline and all alternatives"
                )
        elif self.outcome == "FAILED":
            if self.reason not in (
                "SOURCE_VERIFICATION_FAILURE",
                "NATIVE_EXECUTION_FAILURE",
                "BASELINE_REPRODUCIBILITY_FAILURE",
            ):
                raise ValueError("Invalid counterfactual failure reason")
            if self.reason == "SOURCE_VERIFICATION_FAILURE" and self.results:
                raise ValueError("Source verification must precede native execution")
            if self.reason == "BASELINE_REPRODUCIBILITY_FAILURE":
                if (
                    len(self.results) != 1
                    or self.results[0].run == self.inputs.candidate.report.holdout
                ):
                    raise ValueError("Baseline failure must retain the differing run")
            elif self.results and self.results[0].run != self.inputs.candidate.report.holdout:
                raise ValueError("Baseline mismatch cannot be hidden")
        else:
            raise ValueError("Invalid counterfactual outcome")
