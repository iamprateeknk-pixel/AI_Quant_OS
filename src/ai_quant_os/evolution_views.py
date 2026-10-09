"""Bounded archival genome metadata; no source, prompts or evaluation authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObjectivePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    metric: str = Field(max_length=32)
    direction: Literal["MAXIMIZE", "MINIMIZE"]
    minimum_improvement: str | None = Field(max_length=64)
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GenomePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    genome_ref: str = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    agent_id: str = Field(pattern=r"^A(0[1-9]|1[0-8])$")
    agent_version: str = Field(
        pattern=r"^(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}$", max_length=32
    )
    manifest_ref: str = Field(pattern=r"^agent-manifest:[a-f0-9]{64}$")
    parent_ref: str | None = Field(pattern=r"^agent-genome:[a-f0-9]{64}$")
    state: Literal["ARCHIVED_BASELINE", "UNVERIFIED_CHALLENGER"]
    objective: ObjectivePreview
    source_inventory: tuple[SourceFingerprint, ...] = Field(min_length=1, max_length=3)
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False
    details_truncated: bool

    @model_validator(mode="after")
    def coherent(self):
        if self.details_truncated != (self.objective.minimum_improvement is None):
            raise ValueError("Objective preview omission must be explicit")
        if self.parent_ref == self.genome_ref or (self.parent_ref is None) != (
            self.state == "ARCHIVED_BASELINE"
        ):
            raise ValueError("Genome state must match lineage")
        if len({row.path for row in self.source_inventory}) != len(self.source_inventory):
            raise ValueError("Duplicate source path")
        return self


class EvolutionGenomePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    total: int = Field(strict=True, ge=0, le=1000)
    offset: int = Field(strict=True, ge=0, le=1000)
    limit: int = Field(strict=True, ge=1, le=10)
    next_offset: int | None = Field(strict=True, ge=0, le=1000)
    genomes: tuple[GenomePreview, ...] = Field(max_length=10)

    @model_validator(mode="after")
    def coherent(self):
        expected = self.offset + self.limit if self.offset + self.limit < self.total else None
        if self.next_offset != expected or len(self.genomes) != min(
            self.limit, max(0, self.total - self.offset)
        ):
            raise ValueError("Genome page count mismatch")
        if len({row.genome_ref for row in self.genomes}) != len(self.genomes):
            raise ValueError("Duplicate genome reference")
        return self


def genome_preview(genome) -> GenomePreview:
    improvement = str(genome.objective.minimum_improvement)
    truncated = len(improvement) > 64
    return GenomePreview(
        genome_ref=genome.genome_ref,
        agent_id=genome.manifest.agent_id,
        agent_version=genome.manifest.agent_version,
        manifest_ref=genome.manifest.manifest_ref,
        parent_ref=genome.parent_ref,
        state=genome.state,
        objective=ObjectivePreview(
            metric=genome.objective.metric,
            direction=genome.objective.direction,
            minimum_improvement=None if truncated else improvement,
            protocol_sha256=genome.objective.protocol_sha256,
        ),
        details_truncated=truncated,
        source_inventory=tuple(
            SourceFingerprint(path=row.path, sha256=row.sha256) for row in genome.sources
        ),
    )
