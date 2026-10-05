"""Inert source-only requests for a separate candidate test worker."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.validation_package_contracts import SDK_PATHS, checked_bytes

TEST_PATHS = tuple(sorted((*SDK_PATHS, "candidate.py", "test_candidate.py")))


class CandidateTestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: Literal[
        "ai_quant_os/__init__.py",
        "ai_quant_os/contracts.py",
        "ai_quant_os/market_data.py",
        "ai_quant_os/strategy_contracts.py",
        "candidate.py",
        "test_candidate.py",
    ]
    raw_base64: str = Field(min_length=1, max_length=87384)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_bytes(self):
        maximum = 8192 if self.path in ("candidate.py", "test_candidate.py") else 65536
        checked_bytes(self.raw_base64, self.sha256, maximum)
        return self


class CandidateTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$", strict=True)
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$", strict=True)
    files: tuple[CandidateTestFile, ...] = Field(min_length=6, max_length=6)
    status: Literal["NO_DATA_CANDIDATE_TEST_REQUEST"] = "NO_DATA_CANDIDATE_TEST_REQUEST"
    promotion_allowed: Literal[False] = False

    @field_validator("promotion_allowed", mode="before")
    @classmethod
    def literal_authority(cls, value):
        if value is not False:
            raise ValueError("Candidate test authority must be literal false")
        return value

    @model_validator(mode="after")
    def exact_inventory(self):
        if tuple(row.path for row in self.files) != TEST_PATHS:
            raise ValueError("Candidate test file inventory changed")
        if type(self.promotion_allowed) is not bool:
            raise ValueError("Candidate test authority must be literal false")
        return self

    @property
    def request_ref(self):
        return "candidate-tests:" + sha256(self.model_dump_json().encode()).hexdigest()


class CandidateTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["1.0"]
    request_ref: str = Field(pattern=r"^candidate-tests:[a-f0-9]{64}$")
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    python_version: Literal["3.11.17"]
    pytest_version: Literal["8.4.2"]
    compiled_files: int = Field(ge=0, le=6)
    collected: int = Field(ge=0, le=1024)
    passed: int = Field(ge=0, le=1024)
    failed: int = Field(ge=0, le=3072)
    skipped: int = Field(ge=0, le=1024)
    status: Literal[
        "COMPILE_FAILED",
        "PROPOSED_TESTS_PASSED_UNATTESTED",
        "PROPOSED_TESTS_FAILED_UNATTESTED",
    ]
    isolation_verified: Literal[False]
    execution_authorized: Literal[False]
    promotion_allowed: Literal[False]

    @field_validator(
        "isolation_verified", "execution_authorized", "promotion_allowed", mode="before"
    )
    @classmethod
    def literal_authority(cls, value):
        if value is not False:
            raise ValueError("Candidate test result cannot grant authority")
        return value

    @model_validator(mode="after")
    def coherent_result(self):
        if any(
            type(getattr(self, name)) is not bool
            for name in ("isolation_verified", "execution_authorized", "promotion_allowed")
        ):
            raise ValueError("Candidate test result cannot grant authority")
        if self.status == "PROPOSED_TESTS_PASSED_UNATTESTED" and (
            self.compiled_files,
            self.collected,
            self.passed,
            self.failed,
            self.skipped,
        ) != (6, 6, 6, 0, 0):
            raise ValueError("Successful proposed test counts changed")
        if self.status == "COMPILE_FAILED" and (
            self.compiled_files == 6
            or any((self.collected, self.passed, self.failed, self.skipped))
        ):
            raise ValueError("Compilation failure cannot contain test results")
        return self
