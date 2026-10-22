"""Checked inert validation inputs; no score, executable admission, or capital authority."""

import ast
import base64
from decimal import Context, Decimal, localcontext
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.graph_ledger import GraphRecord
from ai_quant_os.research_data_contracts import ResearchDataReceipt
from ai_quant_os.research_data_parsing import parse_research_csv
from ai_quant_os.strategy_builder_agent import StrategyCandidate
from ai_quant_os.strategy_builder_sources import candidate_for_context
from ai_quant_os.strategy_candidate_contracts import CandidateBundle

SDK_PATHS = (
    "ai_quant_os/__init__.py",
    "ai_quant_os/contracts.py",
    "ai_quant_os/market_data.py",
    "ai_quant_os/strategy_contracts.py",
)


def checked_bytes(encoded: str, digest: str, maximum: int) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid retained base64 bytes") from error
    if (
        not 0 < len(raw) <= maximum
        or base64.b64encode(raw).decode() != encoded
        or sha256(raw).hexdigest() != digest
    ):
        raise ValueError("Retained byte identity or bound mismatch")
    return raw


class DatasetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    receipt: ResearchDataReceipt
    raw_base64: str = Field(min_length=1, max_length=1398104)

    @model_validator(mode="after")
    def exact_dataset(self):
        raw = checked_bytes(self.raw_base64, self.receipt.raw_sha256, 1048576)
        dataset = parse_research_csv(raw)
        with localcontext(Context(prec=28)):
            fingerprint = dataset.fingerprint
        if (
            fingerprint,
            dataset.bars[0].instrument_id,
            len(dataset.bars),
            dataset.bars[0].observed_at,
            dataset.bars[-1].observed_at,
        ) != (
            self.receipt.dataset_fingerprint,
            self.receipt.instrument_id,
            self.receipt.row_count,
            self.receipt.first_observed_at,
            self.receipt.last_observed_at,
        ):
            raise ValueError("Validation dataset differs from its receipt")
        return self


class SDKFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Literal[
        "ai_quant_os/__init__.py",
        "ai_quant_os/contracts.py",
        "ai_quant_os/market_data.py",
        "ai_quant_os/strategy_contracts.py",
    ]
    raw_base64: str = Field(min_length=1, max_length=87384)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def checked_sdk_closure(self):
        # Only trusted local SDK bytes are parsed; candidate files remain inert strings.
        raw = checked_bytes(self.raw_base64, self.sha256, 65536)
        try:
            tree = ast.parse(raw.decode("utf-8"))
        except (SyntaxError, UnicodeError) as error:
            raise ValueError("Invalid retained SDK source") from error
        allowed = {
            "csv",
            "hashlib",
            "io",
            "json",
            "dataclasses",
            "datetime",
            "decimal",
            "pathlib",
            "typing",
            "re",
        }
        dependencies = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level or node.module is None:
                    raise ValueError("Unsupported SDK relative import")
                dependencies.add(node.module)
        internal = dependencies - allowed
        expected = {"ai_quant_os.contracts"} if self.path.endswith("/market_data.py") else set()
        if internal != expected:
            raise ValueError("SDK dependency closure changed")
        return self


class ValidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    rationale: str = Field(min_length=1, max_length=2048)
    spread_bps: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    slippage_bps: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    commission_bps: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    cost_stress_multiplier: Decimal = Field(gt=1, le=100, allow_inf_nan=False)
    walk_forward_train_bars: int = Field(strict=True, ge=8, le=4094)
    walk_forward_test_bars: int = Field(strict=True, ge=2, le=4096)
    monte_carlo_trials: int = Field(strict=True, ge=100, le=100000)
    random_seed: int = Field(strict=True, ge=0, le=4294967295)
    cost_basis: Literal["OPERATOR_DECLARED_SYNTHETIC"] = "OPERATOR_DECLARED_SYNTHETIC"
    financing_costs: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    account_currency_conversion: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    planned_methods: tuple[
        Literal["OUT_OF_SAMPLE", "WALK_FORWARD", "MONTE_CARLO", "COST_STRESS", "REALITY_GAP"], ...
    ] = ("OUT_OF_SAMPLE", "WALK_FORWARD", "MONTE_CARLO", "COST_STRESS", "REALITY_GAP")
    selection_basis: Literal["OPERATOR_SELECTED_NOT_BLINDNESS_PROOF"] = (
        "OPERATOR_SELECTED_NOT_BLINDNESS_PROOF"
    )

    @model_validator(mode="after")
    def complete_methods(self):
        if self.planned_methods != (
            "OUT_OF_SAMPLE",
            "WALK_FORWARD",
            "MONTE_CARLO",
            "COST_STRESS",
            "REALITY_GAP",
        ):
            raise ValueError("Validation plan method inventory mismatch")
        return self


class ValidationPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    graph: GraphRecord
    candidate: CandidateBundle
    training: DatasetSnapshot
    holdout: DatasetSnapshot
    sdk_files: tuple[SDKFile, ...] = Field(min_length=4, max_length=4)
    plan: ValidationPlan
    captured_at: AwareDatetime
    status: Literal["WAIT_FOR_ISOLATED_VALIDATOR"] = "WAIT_FOR_ISOLATED_VALIDATOR"
    validation_status: Literal["NOT_RUN"] = "NOT_RUN"
    tests_status: Literal["NOT_RUN"] = "NOT_RUN"
    isolation_verified: Literal[False] = False
    execution_authorized: Literal[False] = False
    promotion_authorized: Literal[False] = False
    independent_holdout_storage: Literal[False] = False
    runtime_dependency_closure: Literal["NOT_CAPTURED"] = "NOT_CAPTURED"
    execution_adapter: Literal["UNAVAILABLE"] = "UNAVAILABLE"

    @model_validator(mode="after")
    def exact_input_binding(self):
        receipt, context = self.graph.receipt, self.graph.checkpoint.context
        if (receipt.agent_id, receipt.agent_version, receipt.status, context.scope) != (
            "A09",
            "0.2.0",
            "COMPLETED",
            "SANDBOX",
        ):
            raise ValueError("Validation package requires its completed A09 graph")
        output = StrategyCandidate.model_validate_json(receipt.output_json)
        output.require_context(context)
        if (
            self.candidate != candidate_for_context(context)
            or output.candidate_ref != self.candidate.candidate_ref
        ):
            raise ValueError("Validation candidate differs from its original graph")
        training, holdout, hypothesis = (
            self.training.receipt,
            self.holdout.receipt,
            self.candidate.review.hypothesis,
        )
        if (
            training.resource_class,
            training.dataset_ref,
            training.dataset_fingerprint,
            training.instrument_id,
            training.last_observed_at,
        ) != (
            "TRAINING",
            hypothesis.dataset_ref,
            hypothesis.dataset_fingerprint,
            hypothesis.instrument_id,
            hypothesis.development_end,
        ):
            raise ValueError("Validation training differs from original hypothesis")
        if (
            holdout.resource_class != "HOLDOUT"
            or holdout.instrument_id != training.instrument_id
            or holdout.first_observed_at <= training.last_observed_at
            or holdout.dataset_fingerprint == training.dataset_fingerprint
            or holdout.raw_sha256 == training.raw_sha256
        ):
            raise ValueError("Validation requires distinct strictly later same-instrument holdout")
        if (
            self.plan.walk_forward_train_bars + self.plan.walk_forward_test_bars
            > training.row_count
        ):
            raise ValueError("Validation training cannot fit declared walk-forward windows")
        if holdout.row_count < self.plan.walk_forward_test_bars:
            raise ValueError("Validation holdout cannot fit declared test window")
        if tuple(row.path for row in self.sdk_files) != SDK_PATHS:
            raise ValueError("Validation SDK inventory mismatch")
        observed = tuple((row.path.split("/")[-1], row.sha256) for row in self.sdk_files[2:])
        if observed != self.candidate.sdk_source_pins:
            raise ValueError("Validation SDK differs from builder source pins")
        if (
            self.captured_at.utcoffset().total_seconds() != 0
            or max(receipt.created_at, training.received_at, holdout.received_at) > self.captured_at
        ):
            raise ValueError("Validation capture chronology mismatch")
        return self

    @property
    def package_ref(self) -> str:
        return "validation-package:" + sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def request_ref(self) -> str:
        material = json.dumps(
            [
                sha256(self.graph.model_dump_json().encode()).hexdigest(),
                self.training.receipt.dataset_ref,
                self.holdout.receipt.dataset_ref,
                self.plan.model_dump_json(),
            ],
            separators=(",", ":"),
        )
        return "validation-request:" + sha256(material.encode()).hexdigest()

    def metadata(self) -> dict:
        return {
            "mode": "PAPER",
            "scope": "SANDBOX",
            "package_ref": self.package_ref,
            "request_ref": self.request_ref,
            "candidate_ref": self.candidate.candidate_ref,
            "graph_run_id": self.graph.receipt.run_id,
            "captured_at": self.captured_at.isoformat(),
            "training": self.training.receipt.model_dump(mode="json"),
            "holdout": self.holdout.receipt.model_dump(mode="json"),
            "sdk_source_pins": [
                {"path": row.path, "sha256": row.sha256} for row in self.sdk_files
            ],
            "plan": self.plan.model_dump(mode="json"),
            "status": self.status,
            "validation_status": self.validation_status,
            "tests_status": self.tests_status,
            "isolation_verified": False,
            "execution_authorized": False,
            "promotion_authorized": False,
            "independent_holdout_storage": False,
            "runtime_dependency_closure": self.runtime_dependency_closure,
            "execution_adapter": self.execution_adapter,
        }


class ValidationPackageSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Literal[
        "ai_quant_os/__init__.py",
        "ai_quant_os/contracts.py",
        "ai_quant_os/market_data.py",
        "ai_quant_os/strategy_contracts.py",
    ]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ValidationPackageMetadata(BaseModel):
    """Operator-visible package metadata; never includes retained source or dataset bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["PAPER"]
    scope: Literal["SANDBOX"]
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    request_ref: str = Field(pattern=r"^validation-request:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    graph_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    captured_at: AwareDatetime
    training: ResearchDataReceipt
    holdout: ResearchDataReceipt
    sdk_source_pins: tuple[ValidationPackageSourcePin, ...] = Field(min_length=4, max_length=4)
    plan: ValidationPlan
    status: Literal["WAIT_FOR_ISOLATED_VALIDATOR"]
    validation_status: Literal["NOT_RUN"]
    tests_status: Literal["NOT_RUN"]
    isolation_verified: Literal[False]
    execution_authorized: Literal[False]
    promotion_authorized: Literal[False]
    independent_holdout_storage: Literal[False]
    runtime_dependency_closure: Literal["NOT_CAPTURED"]
    execution_adapter: Literal["UNAVAILABLE"]

    @model_validator(mode="after")
    def package_scope_is_consistent(self):
        if (
            self.training.resource_class != "TRAINING"
            or self.holdout.resource_class != "HOLDOUT"
            or self.captured_at < max(self.training.received_at, self.holdout.received_at)
            or tuple(row.path for row in self.sdk_source_pins) != SDK_PATHS
        ):
            raise ValueError("Validation package metadata scope mismatch")
        return self
