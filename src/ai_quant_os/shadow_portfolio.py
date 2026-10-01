"""Frozen candidate assignments and causal, cumulative shadow portfolio observations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from ai_quant_os.contracts import _require_aware
from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.native_bar_evidence import verify_native_bar_result
from ai_quant_os.registered_backtest import RegisteredBacktest
from ai_quant_os.research_candidate_store import CandidateView
from ai_quant_os.research_candidates import CandidateBundle
from ai_quant_os.research_contracts import _identifier
from ai_quant_os.research_store import encode_research


@dataclass(frozen=True)
class ShadowAssignment:
    shadow_id: str
    assigned_at: datetime
    candidate_view: CandidateView
    candidate: CandidateBundle
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["FORWARD_SHADOW_DIAGNOSTIC"] = "FORWARD_SHADOW_DIAGNOSTIC"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        _identifier(self.shadow_id)
        _require_aware(self.assigned_at)
        object.__setattr__(self, "assigned_at", self.assigned_at.astimezone(timezone.utc))
        if (
            self.schema_version != "1.0"
            or self.purpose != "FORWARD_SHADOW_DIAGNOSTIC"
            or self.promotion_allowed is not False
            or type(self.candidate_view) is not CandidateView
            or type(self.candidate) is not CandidateBundle
            or self.candidate_view.manifest != self.candidate.manifest
            or self.candidate_view.state != "CHALLENGER"
            or self.candidate_view.revision != 1
            or self.candidate.report.holdout is None
            or self.candidate.report.selection is None
            or self.assigned_at < self.candidate.report.plan.holdout_scope.last_observed_at
        ):
            raise ValueError(
                "An eligible frozen candidate and post-holdout assignment are required"
            )


@dataclass(frozen=True)
class ShadowRequest:
    shadow_id: str
    command_id: str
    bars: tuple[Bar, ...]

    def __post_init__(self):
        _identifier(self.shadow_id)
        _identifier(self.command_id)
        if (
            type(self.bars) is not tuple
            or not 1 <= len(self.bars) <= 4096
            or any(type(bar) is not Bar for bar in self.bars)
        ):
            raise ValueError("Bounded immutable shadow observations required")
        source = BarDataset(self.bars)
        if source.bars != self.bars or source.duplicate_rows:
            raise ValueError("Shadow observations must be ordered without duplicates")


def verify_forward_source(assignment, bars):
    source = BarDataset(bars)
    if (
        not 2 <= len(bars) <= 4096
        or source.bars != bars
        or source.duplicate_rows
        or bars[0].observed_at <= assignment.assigned_at
        or {bar.instrument_id for bar in bars}
        != {assignment.candidate.report.plan.holdout_scope.instrument_id}
    ):
        raise ValueError("Shadow observations must follow assignment on the frozen instrument")


@dataclass(frozen=True)
class ShadowReport:
    assignment: ShadowAssignment
    request: ShadowRequest
    sequence: int
    previous_sha256: str
    source_bars: tuple[Bar, ...]
    observed_at: datetime
    outcome: Literal["OBSERVED", "FAILED"]
    run: RegisteredBacktest | None
    reason: str | None = None
    schema_version: Literal["1.0"] = "1.0"
    promotion_allowed: Literal[False] = False

    def __post_init__(self):
        _require_aware(self.observed_at)
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        if (
            self.schema_version != "1.0"
            or self.promotion_allowed is not False
            or type(self.assignment) is not ShadowAssignment
            or type(self.request) is not ShadowRequest
            or self.assignment.shadow_id != self.request.shadow_id
            or type(self.sequence) is not int
            or not 1 <= self.sequence <= 1000
            or not isinstance(self.previous_sha256, str)
            or len(self.previous_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.previous_sha256)
            or type(self.source_bars) is not tuple
            or self.source_bars[-len(self.request.bars) :] != self.request.bars
        ):
            raise ValueError("Invalid shadow evidence identity")
        verify_forward_source(self.assignment, self.source_bars)
        if self.source_bars[-1].observed_at > self.observed_at:
            raise ValueError("Shadow observations cannot come from the future")
        if self.sequence == 1 and (
            self.previous_sha256 != "0" * 64 or self.source_bars != self.request.bars
        ):
            raise ValueError("Initial shadow evidence cannot include a hidden prefix")
        parent = self.assignment.candidate.report
        if self.outcome == "OBSERVED":
            if self.reason is not None or self.run is None:
                raise ValueError("Observed shadow evidence requires the frozen implementation")
        elif self.outcome == "FAILED":
            if (
                self.run is not None and self.reason != "CANDIDATE_WITHDRAWN"
            ) or self.reason not in (
                "SOURCE_VERIFICATION_FAILURE",
                "NATIVE_EXECUTION_FAILURE",
                "CANDIDATE_WITHDRAWN",
                "PREFIX_REPRODUCIBILITY_FAILURE",
            ):
                raise ValueError("Invalid shadow failure evidence")
        else:
            raise ValueError("Invalid shadow outcome")
        if self.run is not None:
            if (
                type(self.run) is not RegisteredBacktest
                or self.run.binding != parent.holdout.binding
            ):
                raise ValueError("Shadow frozen implementation mismatch")
            if self.run.result.strategy_ref != parent.holdout.result.strategy_ref:
                raise ValueError("Shadow strategy identity mismatch")
            verify_native_bar_result(
                self.run.result, self.source_bars, parent.plan.quantity, parent.plan.costs
            )

    @property
    def fingerprint(self):
        return sha256(encode_research(self)).hexdigest()
