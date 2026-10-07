"""Declared descriptive screens and immutable candidate validation evidence."""

from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.monte_carlo import MonteCarloResult

GATE_NAMES = (
    "PROPOSED_TESTS",
    "OUT_OF_SAMPLE",
    "WALK_FORWARD",
    "STABILITY",
    "COST_STRESS",
    "MINIMUM_ACTIVITY",
    "MONTE_CARLO",
    "INDEPENDENT_HOLDOUT",
    "SOURCE_ATTESTATION",
    "ADVERSARIAL",
    "REALITY_GAP",
    "FINANCING",
    "ACCOUNT_CONVERSION",
    "INDEPENDENT_REVIEW",
    "SHADOW",
)
PENDING_GATES = GATE_NAMES[7:]
RunReportRef = Annotated[str, Field(pattern=r"^strategy-prefix-run:[a-f0-9]{64}$", strict=True)]
GateEvidenceRef = Annotated[
    str, Field(pattern=r"^(strategy-prefix-run:|sha256:)[a-f0-9]{64}$", strict=True)
]


class CandidateValidationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    minimum_validation_windows: int = Field(default=2, strict=True, ge=2, le=2048)
    minimum_orders_per_window: int = Field(default=1, strict=True, ge=1, le=4096)
    minimum_nonnegative_window_fraction: Decimal = Field(
        default=Decimal("0.75"), ge=0, le=1, allow_inf_nan=False
    )
    minimum_closed_trades: int = Field(default=20, strict=True, ge=20, le=4096)
    monte_carlo_block_size: int = Field(default=4, strict=True, ge=1, le=32)
    lower_tail_percent: int = Field(default=5, strict=True, ge=1, le=25)
    minimum_positive_fraction: Decimal = Field(
        default=Decimal("0.95"), ge=0, le=1, allow_inf_nan=False
    )
    drawdown_limit: Decimal = Field(default=Decimal(100), ge=0, allow_inf_nan=False)
    risk_currency: Literal["USD", "JPY"] = "USD"
    basis: Literal["OPERATOR_DECLARED_DESCRIPTIVE_SCREENS"] = (
        "OPERATOR_DECLARED_DESCRIPTIVE_SCREENS"
    )


class CandidateMonteCarloEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requested_trials: int = Field(strict=True, ge=100, le=100000)
    requested_seed: int = Field(strict=True, ge=0, le=4294967295)
    closed_trade_count: int = Field(strict=True, ge=0, le=4096)
    open_position: Decimal = Field(allow_inf_nan=False)
    sample_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str = Field(min_length=1, max_length=128)
    result: MonteCarloResult | None

    @model_validator(mode="after")
    def result_binding(self):
        if self.result is None:
            if self.state != "PENDING" or self.reason != "DECLARED_COMPUTATION_CAPACITY_EXCEEDED":
                raise ValueError("Absent simulation requires an explicit capacity pending reason")
        elif (
            self.result.spec.iterations,
            self.result.spec.seed,
            self.result.closed_trade_count,
            self.result.open_position,
            self.result.sample_fingerprint,
            self.result.state,
            self.result.reason,
        ) != (
            self.requested_trials,
            self.requested_seed,
            self.closed_trade_count,
            self.open_position,
            self.sample_fingerprint,
            self.state,
            self.reason,
        ):
            raise ValueError("Monte Carlo evidence differs from its requested sample and plan")
        return self


class CandidateValidationGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    state: Literal["PASS", "FAIL", "PENDING"]
    reason: str = Field(min_length=1, max_length=128)
    evidence_refs: tuple[GateEvidenceRef, ...] = Field(min_length=1, max_length=2050)


class CandidateValidationScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    test_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    holdout_report_ref: str = Field(pattern=r"^strategy-prefix-run:[a-f0-9]{64}$")
    window_report_refs: tuple[RunReportRef, ...] = Field(min_length=1, max_length=2048)
    policy: CandidateValidationPolicy
    holdout_net_pnl: Decimal = Field(allow_inf_nan=False)
    development_net_pnl: Decimal = Field(allow_inf_nan=False)
    nonnegative_window_fraction: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    pnl_currency: Literal["USD", "JPY"]
    monte_carlo: CandidateMonteCarloEvidence
    gates: tuple[CandidateValidationGate, ...] = Field(min_length=15, max_length=15)
    outcome: Literal["REJECTED", "VALIDATION_INCOMPLETE"]
    reasons: tuple[str, ...] = Field(min_length=1, max_length=15)
    evidence_basis: Literal["UNATTESTED_METADATA_AND_CHECKED_NATIVE_NUMERIC_LEDGERS"] = (
        "UNATTESTED_METADATA_AND_CHECKED_NATIVE_NUMERIC_LEDGERS"
    )
    selection_data_overlap: Literal[True] = True
    statistical_independence: Literal["UNASSESSED"] = "UNASSESSED"
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator(
        "selection_data_overlap", "isolation_verified", "promotion_allowed", mode="before"
    )
    @classmethod
    def exact_authority(cls, value, info):
        expected = info.field_name == "selection_data_overlap"
        if value is not expected:
            raise ValueError("Candidate scorecards cannot grant authority or remove overlap")
        return value

    @model_validator(mode="after")
    def complete_gates(self):
        if tuple(row.name for row in self.gates) != GATE_NAMES:
            raise ValueError("Required candidate validation gates cannot be skipped or reordered")
        if any(row.state != "PENDING" for row in self.gates if row.name in PENDING_GATES):
            raise ValueError("Unverified independent acceptance gates cannot pass")
        failed = tuple(row.name + ":" + row.reason for row in self.gates if row.state == "FAIL")
        pending = tuple(row.name + ":" + row.reason for row in self.gates if row.state == "PENDING")
        if self.outcome != ("REJECTED" if failed else "VALIDATION_INCOMPLETE") or self.reasons != (
            failed or pending
        ):
            raise ValueError("Candidate outcome must retain measured failures or missing evidence")
        if (self.gates[6].state, self.gates[6].reason) != (
            self.monte_carlo.state,
            self.monte_carlo.reason,
        ):
            raise ValueError("Monte Carlo gate differs from retained simulation")
        if self.pnl_currency != self.policy.risk_currency:
            raise ValueError("Screen policy currency differs from native quote P&L")
        if len(set(self.window_report_refs)) != len(self.window_report_refs):
            raise ValueError("Development report identity was reused")
        return self

    @property
    def scorecard_ref(self) -> str:
        return "candidate-validation:" + sha256(self.model_dump_json().encode()).hexdigest()
