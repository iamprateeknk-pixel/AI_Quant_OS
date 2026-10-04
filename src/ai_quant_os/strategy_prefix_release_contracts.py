"""Pinned recovery archives and released-input observations; no execution authority."""

import base64
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.strategy_prefix_execution_contracts import StrategyPrefixExecutionReceipt

Nonnegative = Annotated[int, Field(strict=True, ge=0)]
FILES = {
    "request.json": 524288,
    "stage.json": 65536,
    "container.json": 8192,
    "create.json": 65536,
    "create-attempt.json": 8192,
    "start-attempt.json": 8192,
    "target.json": 2048,
    "execution.json": 65536,
}


class PrefixReleaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal[
        "request.json",
        "stage.json",
        "container.json",
        "create.json",
        "create-attempt.json",
        "start-attempt.json",
        "target.json",
        "execution.json",
    ]
    identity: tuple[Nonnegative, Nonnegative, Nonnegative, Nonnegative, Literal[1], Nonnegative]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_base64: str = Field(max_length=699052)

    @field_validator("identity", mode="before")
    @classmethod
    def exact_identity(cls, value):
        if (
            type(value) not in (tuple, list)
            or len(value) != 6
            or any(type(item) is not int for item in value)
        ):
            raise ValueError("Exact integer file identity required")
        return value

    def content(self) -> bytes:
        try:
            raw = base64.b64decode(self.data_base64, validate=True)
        except ValueError as error:
            raise ValueError("Invalid release archive encoding") from error
        if (
            len(raw) > FILES[self.name]
            or len(raw) != self.identity[2]
            or sha256(raw).hexdigest() != self.sha256
            or base64.b64encode(raw).decode() != self.data_base64
            or self.identity[5] > 4095
        ):
            raise ValueError("Release archive file content/identity mismatch")
        return raw

    @model_validator(mode="after")
    def bound_content(self):
        self.content()
        return self


class PrefixReleaseDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=4096)
    identity: tuple[Nonnegative, Nonnegative]


class StrategyPrefixReleaseBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    job_name: str = Field(pattern=r"^strategy-job-[a-f0-9]{32}$")
    index: int = Field(strict=True, ge=2, le=4095)
    parents: tuple[PrefixReleaseDirectory, ...] = Field(min_length=1, max_length=128)
    job_identity: tuple[Nonnegative, Nonnegative]
    files: tuple[PrefixReleaseFile, ...] = Field(min_length=8, max_length=8)
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt: StrategyPrefixExecutionReceipt
    state: Literal["REMOVED_PREFIX_RECOVERY_ARCHIVED"] = "REMOVED_PREFIX_RECOVERY_ARCHIVED"
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def inventory(self):
        if tuple(row.name for row in self.files) != tuple(sorted(FILES)):
            raise ValueError("Exact canonical release file inventory required")
        for row in self.files:
            PrefixReleaseFile.model_validate_json(row.model_dump_json())
        receipt = StrategyPrefixExecutionReceipt.model_validate_json(self.receipt.model_dump_json())
        files = {row.name: row for row in self.files}
        if (
            files["execution.json"].content() != receipt.model_dump_json().encode()
            or self.receipt_sha256 != files["execution.json"].sha256
            or self.job_name != receipt.ownership.job_name
        ):
            raise ValueError("Release archive differs from independently pinned completion")
        return self

    @property
    def release_sha256(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class StrategyPrefixReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    release_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner: str = Field(pattern=r"^[a-f0-9]{32}$")
    container_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    invocation_ref: str = Field(pattern=r"^candidate-invocation:[a-f0-9]{64}$")
    state: Literal["PREFIX_INPUTS_RELEASED"] = "PREFIX_INPUTS_RELEASED"
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False
