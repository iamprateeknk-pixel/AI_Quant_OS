"""Frozen prospective datasets and measurement definitions, never evaluator code."""

import json
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.agent_genomes import AgentGenome, EvaluationObjective, MetricCriterion
from ai_quant_os.agent_registry import load_agent_catalog


MAX_DATASET_BYTES = 1048576
VERSION = r"^(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}$"
METHODS = {
    "CORRECTNESS": "EXACT_OBJECT_MATCH",
    "CALIBRATION_ERROR": "BINARY_BRIER",
    "EVIDENCE_AGE_MS": "MAX_EVIDENCE_AGE",
    "RELIABILITY": "SUCCESS_RATE",
    "LATENCY_MS": "P95_WALL_MS",
    "TOOL_ERROR_RATE": "TOOL_FAILURE_RATE",
    "COST_USD": "ACTUAL_USAGE_USD",
    "REGIME_CORRECTNESS": "REGIME_EXACT_OBJECT_MATCH",
    "INTEGRITY_CORRECTNESS": "INTEGRITY_EVIDENCE_MATCH",
}


def encode_protocol_artifact(artifact: BaseModel) -> bytes:
    return json.dumps(
        artifact.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


class _Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("execution_verified", "promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("Evaluation artifacts require literal false authority")
        return False


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    input_json: str = Field(min_length=2, max_length=16384)
    expected_json: str = Field(min_length=2, max_length=16384)

    @model_validator(mode="after")
    def canonical_objects(self) -> "EvaluationCase":
        for raw in (self.input_json, self.expected_json):
            try:
                value = json.loads(raw)
                canonical = json.dumps(
                    value, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            except RecursionError:
                raise ValueError("Case JSON nesting exceeds parser capacity") from None
            if not isinstance(value, dict) or len(raw.encode()) > 16384 or canonical != raw:
                raise ValueError("Case input/expected values must be bounded canonical objects")
        return self

    @property
    def input_sha256(self) -> str:
        return sha256(self.input_json.encode()).hexdigest()


class EvolutionDataset(_Artifact):
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    dataset_version: str = Field(pattern=VERSION)
    agent_id: Literal["A02", "A07"]
    resource_class: Literal["TRAINING", "HOLDOUT"]
    provenance: Literal[
        "SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT", "RECORDED_REFERENCE", "REVIEWED_REFERENCE"
    ]
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def case_inventory(self) -> "EvolutionDataset":
        ids = tuple(row.case_id for row in self.cases)
        if ids != tuple(sorted(set(ids))) or len({row.input_sha256 for row in self.cases}) != len(
            self.cases
        ):
            raise ValueError("Dataset cases/inputs must be unique and sorted")
        if len(encode_protocol_artifact(self)) > MAX_DATASET_BYTES:
            raise ValueError("Dataset serialized bytes exceed bound")
        return self

    @property
    def dataset_ref(self) -> str:
        return "evolution-dataset:" + sha256(encode_protocol_artifact(self)).hexdigest()


class ProtocolCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    criterion: MetricCriterion
    method: Literal[
        "EXACT_OBJECT_MATCH",
        "BINARY_BRIER",
        "MAX_EVIDENCE_AGE",
        "SUCCESS_RATE",
        "P95_WALL_MS",
        "TOOL_FAILURE_RATE",
        "ACTUAL_USAGE_USD",
        "REGIME_EXACT_OBJECT_MATCH",
        "INTEGRITY_EVIDENCE_MATCH",
    ]
    maximum_regression: Decimal = Field(ge=0, le=1000000, allow_inf_nan=False)

    @model_validator(mode="after")
    def measurement(self) -> "ProtocolCriterion":
        if self.method != METHODS[self.criterion.metric]:
            raise ValueError("Metric measurement method mismatch")
        if (
            self.criterion.metric
            in {
                "CORRECTNESS",
                "CALIBRATION_ERROR",
                "RELIABILITY",
                "TOOL_ERROR_RATE",
                "REGIME_CORRECTNESS",
                "INTEGRITY_CORRECTNESS",
            }
            and self.maximum_regression > 1
        ):
            raise ValueError("Rate regression cannot exceed metric units")
        return self


class EvaluationProtocol(_Artifact):
    protocol_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    protocol_version: str = Field(pattern=VERSION)
    agent_id: Literal["A02", "A07"]
    output_type: str = Field(min_length=1, max_length=128)
    dataset_ref: str = Field(pattern=r"^evolution-dataset:[a-f0-9]{64}$")
    primary_metric: str = Field(min_length=1, max_length=64)
    criteria: tuple[ProtocolCriterion, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def measurement_inventory(self) -> "EvaluationProtocol":
        metrics = tuple(row.criterion.metric for row in self.criteria)
        if metrics != tuple(sorted(set(metrics))) or self.primary_metric not in metrics:
            raise ValueError("Protocol criteria must be unique/sorted and include primary metric")
        if self.output_type != load_agent_catalog().get(self.agent_id, "0.2.0").output_type:
            raise ValueError("Protocol output differs from fixed agent contract")
        if len(encode_protocol_artifact(self)) > 8192:
            raise ValueError("Protocol serialized bytes exceed bound")
        return self

    @property
    def protocol_sha256(self) -> str:
        return sha256(encode_protocol_artifact(self)).hexdigest()

    @property
    def objective(self) -> EvaluationObjective:
        criterion = next(
            row.criterion for row in self.criteria if row.criterion.metric == self.primary_metric
        )
        return EvaluationObjective(**criterion.model_dump(), protocol_sha256=self.protocol_sha256)


def require_protocol_genome(protocol: EvaluationProtocol, genome: AgentGenome) -> None:
    if (
        protocol.agent_id != genome.manifest.agent_id
        or protocol.output_type != genome.manifest.output_type
        or protocol.objective != genome.objective
    ):
        raise ValueError("Genome does not match retained protocol identity/objective")
