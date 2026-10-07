"""Original-source prerequisite failures without invented downstream observations."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.candidate_dataset_views import _checked_package
from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_test_jobs import candidate_test_request
from ai_quant_os.candidate_validation_contracts import GATE_NAMES
from ai_quant_os.validation_package_contracts import ValidationPackage


class CandidatePrerequisiteFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$", strict=True)
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$", strict=True)
    test_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    test_result: CandidateTestResult
    outcome: Literal["PREREQUISITE_FAILED"] = "PREREQUISITE_FAILED"
    reason: Literal["COMPILE_FAILED", "PROPOSED_TESTS_FAILED_UNATTESTED"]
    unexecuted_gates: tuple[str, ...] = Field(min_length=14, max_length=14)
    evidence_basis: Literal["UNATTESTED_SOURCE_ONLY_TEST_METADATA"] = (
        "UNATTESTED_SOURCE_ONLY_TEST_METADATA"
    )
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("isolation_verified", "promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Prerequisite failures cannot grant authority")
        return value

    @model_validator(mode="after")
    def exact_observation(self):
        tested = self.test_result
        if (
            self.package_ref != tested.package_ref
            or self.candidate_ref != tested.candidate_ref
            or self.reason != tested.status
            or self.test_result_sha256 != sha256(tested.model_dump_json().encode()).hexdigest()
            or self.unexecuted_gates != GATE_NAMES[1:]
        ):
            raise ValueError("Prerequisite failure observation binding changed")
        if tested.status == "PROPOSED_TESTS_FAILED_UNATTESTED" and (
            tested.compiled_files,
            tested.collected,
            tested.passed,
            tested.failed,
            tested.skipped,
        ) == (6, 6, 6, 0, 0):
            raise ValueError("Successful test counts cannot be relabeled as failure")
        return self

    @property
    def failure_ref(self) -> str:
        return (
            "candidate-prerequisite-failure:" + sha256(self.model_dump_json().encode()).hexdigest()
        )


def record_prerequisite_failure(
    package: ValidationPackage, tested: CandidateTestResult
) -> CandidatePrerequisiteFailure:
    package = _checked_package(package)
    if type(tested) is not CandidateTestResult:
        raise ValueError("Original proposed-test observation required")
    tested = CandidateTestResult.model_validate_json(tested.model_dump_json())
    request = candidate_test_request(package)
    if (tested.package_ref, tested.candidate_ref, tested.request_ref) != (
        package.package_ref,
        package.candidate.candidate_ref,
        request.request_ref,
    ):
        raise ValueError("Prerequisite failure belongs to different original source inputs")
    return CandidatePrerequisiteFailure(
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        test_result_sha256=sha256(tested.model_dump_json().encode()).hexdigest(),
        test_result=tested,
        reason=tested.status,
        unexecuted_gates=GATE_NAMES[1:],
    )


def verify_prerequisite_failure(
    package: ValidationPackage, tested: CandidateTestResult, failure: CandidatePrerequisiteFailure
) -> None:
    if type(failure) is not CandidatePrerequisiteFailure:
        raise ValueError("Typed prerequisite failure required")
    failure = CandidatePrerequisiteFailure.model_validate_json(failure.model_dump_json())
    if failure != record_prerequisite_failure(package, tested):
        raise ValueError("Prerequisite failure differs from original test observation")
