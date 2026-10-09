"""Bounded metadata for archived implementations; registration is not approval."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArchivedStrategySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StrategyParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z_]{0,31}$")
    value: int = Field(strict=True, ge=0, le=4096)


class ArchivedStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_ref: str = Field(max_length=128, pattern=r"^[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+$")
    input_kind: Literal["BAR", "TICK"]
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["ARCHIVED_IMPLEMENTATION"] = "ARCHIVED_IMPLEMENTATION"
    parameters: tuple[StrategyParameter, ...] = Field(max_length=16)
    source_inventory: tuple[ArchivedStrategySource, ...] = Field(min_length=1, max_length=16)
    details_truncated: bool
    promotion_allowed: Literal[False] = False


class StrategyArchivePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    total: int = Field(strict=True, ge=0, le=1000)
    offset: int = Field(strict=True, ge=0, le=1000)
    limit: int = Field(strict=True, ge=1, le=10)
    next_offset: int | None = Field(strict=True, ge=0, le=1000)
    strategies: tuple[ArchivedStrategy, ...] = Field(max_length=10)

    @model_validator(mode="after")
    def coherent(self):
        next_offset = self.offset + self.limit if self.offset + self.limit < self.total else None
        if self.next_offset != next_offset or len(self.strategies) != min(
            self.limit, max(0, self.total - self.offset)
        ):
            raise ValueError("Strategy page count mismatch")
        if len({row.strategy_ref for row in self.strategies}) != len(self.strategies):
            raise ValueError("Duplicate strategy reference")
        return self


def strategy_preview(implementation) -> ArchivedStrategy:
    return ArchivedStrategy(
        strategy_ref=implementation.strategy_ref,
        input_kind=implementation.input_kind,
        implementation_sha256=implementation.fingerprint,
        parameters=tuple(
            StrategyParameter(name=name, value=value)
            for name, value in implementation.parameter_defaults
        ),
        source_inventory=tuple(
            ArchivedStrategySource(path=row.path[:128], sha256=row.sha256)
            for row in implementation.source_files
        ),
        details_truncated=any(len(row.path) > 128 for row in implementation.source_files),
    )
