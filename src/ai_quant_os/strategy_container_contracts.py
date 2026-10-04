"""Local container ownership metadata; no execution or promotion certificate."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyContainerOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    owner: str = Field(pattern=r"^[a-f0-9]{32}$")
    job_path: str = Field(min_length=1, max_length=4096)
    job_name: str = Field(pattern=r"^strategy-job-[a-f0-9]{32}$")
    invocation_ref: str = Field(pattern=r"^candidate-invocation:[a-f0-9]{64}$")
    stage_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    worker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def local_job_path(self):
        if (
            not Path(self.job_path).is_absolute()
            or Path(self.job_path).name != self.job_name
            or any(character in self.job_path for character in ("\x00", "\n", "\r"))
            or ".." in Path(self.job_path).parts
        ):
            raise ValueError("Container ownership requires an exact absolute local job path")
        return self

    @property
    def container_name(self) -> str:
        return "ai-quant-strategy-" + self.owner


class StrategyContainerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    ownership: StrategyContainerOwnership
    state: Literal["CREATE_INTENT", "CREATE_ACK", "REMOVE_INTENT", "REMOVED"]
    container_id: str = Field(pattern=r"^(?:[a-f0-9]{64})?$")
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def intent_identity(self):
        if bool(self.container_id) != (self.state != "CREATE_INTENT"):
            raise ValueError("Container acknowledgement requires its exact immutable ID")
        return self
