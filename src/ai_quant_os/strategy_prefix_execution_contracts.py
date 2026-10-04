"""Unattested owned prefix execution evidence; never an isolation certificate."""

from datetime import datetime
from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.strategy_container_contracts import (
    StrategyContainerOwnership,
    StrategyContainerRecord,
)
from ai_quant_os.strategy_image_content import encode_metadata, require_digest


def strategy_start_request(
    ownership: StrategyContainerOwnership, create_sha256: str, container_id: str
) -> bytes:
    require_digest(create_sha256, prefix="")
    if type(container_id) is not str or not re.fullmatch("[a-f0-9]{64}", container_id):
        raise ValueError("Exact immutable start identity required")
    return encode_metadata(
        {
            "schema_version": "1.0",
            "owner": ownership.owner,
            "container_id": container_id,
            "create_sha256": create_sha256,
            "invocation_ref": ownership.invocation_ref,
            "arguments": ["container", "start", "--attach", container_id],
            "timeout_seconds": 45,
            "output_limit": 65536,
            "state": "START_REQUEST_MAY_HAVE_BEEN_SENT",
            "execution_verified": False,
            "promotion_allowed": False,
        }
    )


class StrategyContainerExit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    container_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["exited"]
    running: Literal[False]
    paused: Literal[False]
    restarting: Literal[False]
    oom_killed: Literal[False]
    dead: Literal[False]
    pid: int = Field(strict=True, ge=0, le=0)
    exit_code: int = Field(strict=True, ge=0, le=0)
    error: Literal[""]
    started_at: str = Field(min_length=1, max_length=64)
    finished_at: str = Field(min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def strict_state(cls, value):
        if type(value) is not dict or any(
            type(value.get(key)) is not bool or value[key]
            for key in ("running", "paused", "restarting", "oom_killed", "dead")
        ):
            raise ValueError("Successful exit requires strict false state flags")
        return value

    @model_validator(mode="after")
    def clocks(self):
        start, finish = (
            datetime.fromisoformat(value) for value in (self.started_at, self.finished_at)
        )
        if (
            any(
                clock.year < 2000 or clock.tzinfo is None or clock.utcoffset().total_seconds() != 0
                for clock in (start, finish)
            )
            or finish < start
        ):
            raise ValueError("Exit clock identity/chronology invalid")
        return self

    @classmethod
    def from_inspection(cls, value):
        try:
            state = value["State"]
            return cls(
                container_id=value["Id"],
                status=state["Status"],
                running=state["Running"],
                paused=state["Paused"],
                restarting=state["Restarting"],
                oom_killed=state["OOMKilled"],
                dead=state["Dead"],
                pid=state["Pid"],
                exit_code=state["ExitCode"],
                error=state["Error"],
                started_at=state["StartedAt"],
                finished_at=state["FinishedAt"],
            )
        except (KeyError, TypeError) as error:
            raise ValueError("Complete successful exit metadata required") from error

    @property
    def metadata_sha256(self):
        return sha256(encode_metadata(self.model_dump(mode="json"))).hexdigest()


class StrategyPrefixExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["UNATTESTED_PREFIX_COMPLETED_AND_REMOVED"] = (
        "UNATTESTED_PREFIX_COMPLETED_AND_REMOVED"
    )
    ownership: StrategyContainerOwnership
    create_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exit_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: CandidateTargetOutput
    exit: StrategyContainerExit
    removal: StrategyContainerRecord
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def exact_links(self):
        ownership = StrategyContainerOwnership.model_validate_json(self.ownership.model_dump_json())
        target = CandidateTargetOutput.model_validate_json(self.target.model_dump_json())
        removal = StrategyContainerRecord.model_validate_json(self.removal.model_dump_json())
        exit = StrategyContainerExit.model_validate_json(self.exit.model_dump_json())
        if (
            ownership != removal.ownership
            or removal.state != "REMOVED"
            or self.exit.container_id != removal.container_id
            or target.invocation_ref != ownership.invocation_ref
            or sha256(target.model_dump_json().encode()).hexdigest() != self.output_sha256
            or self.exit.metadata_sha256 != self.exit_metadata_sha256
            or sha256(
                strategy_start_request(ownership, self.create_sha256, exit.container_id)
            ).hexdigest()
            != self.start_sha256
        ):
            raise ValueError("Prefix completion ownership/output/exit/removal binding differs")
        return self
