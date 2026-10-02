"""Immutable archival agent genomes; no construction of executable implementations."""

import json
import re
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.agent_registry import AgentManifest, AgentRegistry
from ai_quant_os.agent_sources import AGENT_SOURCE_PATHS
from ai_quant_os.repair_image_inputs import read_repair_image_input


MAX_GENOME_BYTES = 262144
GENOME_REF = re.compile(r"agent-genome:[a-f0-9]{64}")


def _version(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}", version):
        raise ValueError("Genome version must be canonical semantic version")
    return tuple(int(part) for part in version.split("."))


class MetricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    metric: Literal[
        "CORRECTNESS",
        "CALIBRATION_ERROR",
        "EVIDENCE_AGE_MS",
        "RELIABILITY",
        "LATENCY_MS",
        "TOOL_ERROR_RATE",
        "COST_USD",
        "REGIME_CORRECTNESS",
    ]
    direction: Literal["MAXIMIZE", "MINIMIZE"]
    minimum_improvement: Decimal = Field(ge=0, le=1000000, allow_inf_nan=False)

    @model_validator(mode="after")
    def metric_policy(self) -> "MetricCriterion":
        maximized = self.metric in {"CORRECTNESS", "RELIABILITY", "REGIME_CORRECTNESS"}
        expected = "MAXIMIZE" if maximized else "MINIMIZE"
        rates = {
            "CORRECTNESS",
            "CALIBRATION_ERROR",
            "RELIABILITY",
            "TOOL_ERROR_RATE",
            "REGIME_CORRECTNESS",
        }
        if self.direction != expected or (self.metric in rates and self.minimum_improvement > 1):
            raise ValueError("Objective direction or improvement differs from metric units")
        return self


class EvaluationObjective(MetricCriterion):
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GenomeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(max_length=128)
    content: str = Field(max_length=65536)

    @model_validator(mode="after")
    def bounded_content(self) -> "GenomeSource":
        if not 0 < len(self.content.encode()) <= 65536:
            raise ValueError("Genome source exceeds byte bound")
        return self

    @property
    def sha256(self) -> str:
        return sha256(self.content.encode()).hexdigest()


class AgentGenome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    manifest: AgentManifest
    sources: tuple[GenomeSource, ...] = Field(min_length=1, max_length=3)
    parent_ref: str | None = Field(default=None, pattern=r"^agent-genome:[a-f0-9]{64}$")
    objective: EvaluationObjective
    model_route: Literal["QUANTITATIVE", "JEV", "OPENAI"] = "QUANTITATIVE"
    model_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    prompt_template: str | None = Field(default=None, min_length=1, max_length=32768)
    feature_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("execution_verified", "promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("Genome authority flags require literal false")
        return False

    @model_validator(mode="after")
    def archive_policy(self) -> "AgentGenome":
        _version(self.manifest.agent_version)
        expected = AGENT_SOURCE_PATHS.get((self.manifest.agent_id, "0.2.0"))
        if expected is None or tuple(row.path for row in self.sources) != tuple(sorted(expected)):
            raise ValueError("Genome source inventory differs from fixed agent ownership")
        if (
            self.feature_refs != tuple(sorted(set(self.feature_refs)))
            or not set(self.feature_refs).issubset(self.manifest.required_inputs)
            or self.manifest.allowed_tools != tuple(sorted(self.manifest.allowed_tools))
        ):
            raise ValueError("Genome features/tools must be canonical declared capabilities")
        if self.model_route == "QUANTITATIVE":
            if self.model_ref is not None or self.prompt_template is not None:
                raise ValueError("Quantitative genome cannot declare a model prompt")
        elif self.model_ref is None or self.prompt_template is None:
            raise ValueError("Model genome requires an explicit model reference and prompt")
        if len(encode_genome(self)) > MAX_GENOME_BYTES:
            raise ValueError("Serialized genome exceeds bound")
        return self

    @property
    def genome_ref(self) -> str:
        return "agent-genome:" + sha256(encode_genome(self)).hexdigest()

    @property
    def state(self) -> str:
        return "ARCHIVED_BASELINE" if self.parent_ref is None else "UNVERIFIED_CHALLENGER"


def encode_genome(genome: AgentGenome) -> bytes:
    return json.dumps(
        genome.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def capture_baseline_genome(
    registry: AgentRegistry, agent_id: str, objective: EvaluationObjective
) -> AgentGenome:
    paths = AGENT_SOURCE_PATHS.get((agent_id, "0.2.0"))
    if paths is None:
        raise ValueError("Agent has no declared implemented source baseline")
    manifest = registry.get(agent_id, "0.2.0")
    package = Path(__file__).resolve().parent
    return AgentGenome(
        manifest=manifest,
        objective=objective,
        sources=tuple(
            GenomeSource(
                path=path,
                content=read_repair_image_input(package / Path(path).name, 65536).decode(),
            )
            for path in sorted(paths)
        ),
        feature_refs=tuple(sorted(manifest.required_inputs)),
    )


def require_genome_child(parent: AgentGenome, child: AgentGenome) -> None:
    if (
        child.parent_ref != parent.genome_ref
        or child.manifest.agent_id != parent.manifest.agent_id
        or _version(child.manifest.agent_version) <= _version(parent.manifest.agent_version)
        or child.objective != parent.objective
        or not set(child.manifest.allowed_tools).issubset(parent.manifest.allowed_tools)
        or child.manifest.timeout_seconds > parent.manifest.timeout_seconds
    ):
        raise ValueError("Genome parent, version or inherited policy mismatch")
    excluded = {"agent_version", "allowed_tools", "timeout_seconds"}
    if child.manifest.model_dump(exclude=excluded) != parent.manifest.model_dump(exclude=excluded):
        raise ValueError("Genome cannot change the original agent role or contract")
    ignored = {"manifest", "parent_ref"}
    if child.model_dump(exclude=ignored) == parent.model_dump(exclude=ignored) and (
        child.manifest.allowed_tools == parent.manifest.allowed_tools
        and child.manifest.timeout_seconds == parent.manifest.timeout_seconds
    ):
        raise ValueError("Genome version change requires a changed implementation or configuration")
