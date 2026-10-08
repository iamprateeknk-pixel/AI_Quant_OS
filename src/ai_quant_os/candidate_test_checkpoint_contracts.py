"""Retained source-test custody checkpoints carry no runtime or acceptance authority."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.candidate_validation_job_contracts import Digest

Checkpoint = Literal[
    "NO_CREATE_INTENT_RETAINED",
    "CREATE_OUTCOME_UNCONFIRMED",
    "CREATE_ACK_RETAINED",
    "CREATED_ADMISSION_RETAINED",
    "START_OUTCOME_UNCONFIRMED",
    "TERMINAL_EXIT_RETAINED",
    "WORKER_OUTPUT_RETAINED",
    "REMOVAL_OUTCOME_UNCONFIRMED",
    "REMOVAL_ACK_RETAINED",
    "RESULT_RETAINED",
]
FileName = Literal[
    "create-intent.json",
    "create-ack.json",
    "created-inspect.json",
    "start-intent.json",
    "terminal-inspect.json",
    "worker-result.json",
    "remove-intent.json",
    "removed.json",
    "result.json",
]
CHECKPOINT_FILES = (
    ("create-intent.json", "CREATE_OUTCOME_UNCONFIRMED", ()),
    ("create-ack.json", "CREATE_ACK_RETAINED", ("create-intent.json",)),
    ("created-inspect.json", "CREATED_ADMISSION_RETAINED", ("create-ack.json",)),
    ("start-intent.json", "START_OUTCOME_UNCONFIRMED", ("created-inspect.json",)),
    ("terminal-inspect.json", "TERMINAL_EXIT_RETAINED", ("start-intent.json",)),
    ("worker-result.json", "WORKER_OUTPUT_RETAINED", ("terminal-inspect.json",)),
    ("remove-intent.json", "REMOVAL_OUTCOME_UNCONFIRMED", ("create-ack.json",)),
    ("removed.json", "REMOVAL_ACK_RETAINED", ("remove-intent.json",)),
    ("result.json", "RESULT_RETAINED", ("worker-result.json", "removed.json")),
)


def checkpoint_for_files(names: frozenset[str]) -> Checkpoint:
    checkpoint = "NO_CREATE_INTENT_RETAINED"
    for name, observed, required in CHECKPOINT_FILES:
        if name in names:
            if not set(required) <= names:
                raise ValueError("Retained worker checkpoint prerequisites unavailable")
            checkpoint = observed
    return checkpoint


class CandidateTestCheckpointFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: FileName
    sha256: Digest


class CandidateTestCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$", strict=True)
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$", strict=True)
    request_ref: str = Field(pattern=r"^candidate-tests:[a-f0-9]{64}$", strict=True)
    manifest_sha256: Digest
    owner: str = Field(pattern=r"^[a-f0-9]{32}$", strict=True)
    container_id: Digest | None = None
    checkpoint: Checkpoint
    files: tuple[CandidateTestCheckpointFile, ...] = Field(max_length=9)
    test_status: (
        Literal[
            "COMPILE_FAILED", "PROPOSED_TESTS_PASSED_UNATTESTED", "PROPOSED_TESTS_FAILED_UNATTESTED"
        ]
        | None
    ) = None
    evidence_basis: Literal["ORIGINAL_LOCAL_RETAINED_WORKER_CUSTODY"] = (
        "ORIGINAL_LOCAL_RETAINED_WORKER_CUSTODY"
    )
    worker_failure_confirmed: Literal[False] = False
    cleanup_verified: Literal[False] = False
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator(
        "worker_failure_confirmed",
        "cleanup_verified",
        "isolation_verified",
        "promotion_allowed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Retained checkpoints cannot certify failure, cleanup or acceptance")
        return value

    @model_validator(mode="after")
    def coherent_checkpoint(self):
        names = tuple(row.name for row in self.files)
        if names != tuple(sorted(set(names))) or self.checkpoint != checkpoint_for_files(
            frozenset(names)
        ):
            raise ValueError("Checkpoint differs from ordered original evidence inventory")
        if (self.container_id is not None) != ("create-ack.json" in names):
            raise ValueError("Only acknowledged creation carries an immutable container ID")
        if (self.test_status is not None) != ("worker-result.json" in names):
            raise ValueError("Only retained worker output carries a test status")
        return self

    @property
    def checkpoint_ref(self) -> str:
        return "candidate-test-checkpoint:" + sha256(self.model_dump_json().encode()).hexdigest()
