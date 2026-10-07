"""Explicit retrospective development windows, never relabeled holdout packages."""

import base64
from dataclasses import dataclass
from decimal import Context, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_quant_os.candidate_test_contracts import CandidateTestResult
from ai_quant_os.candidate_test_jobs import candidate_test_request
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv
from ai_quant_os.validation_package_contracts import ValidationPackage


class CandidateDatasetWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$", strict=True)
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$", strict=True)
    test_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    dataset_ref: str = Field(pattern=r"^research-data:[a-f0-9]{64}$", strict=True)
    original_dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    window_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    fold_index: int = Field(strict=True, ge=0, le=2047)
    history_start: int = Field(strict=True, ge=0, le=4092)
    validation_start: int = Field(strict=True, ge=8, le=4094)
    validation_stop: int = Field(strict=True, ge=9, le=4096)
    complete_declared_window: bool = Field(strict=True)
    resource_class: Literal["TRAINING"] = "TRAINING"
    purpose: Literal["RETROSPECTIVE_DEVELOPMENT_WALK_FORWARD"] = (
        "RETROSPECTIVE_DEVELOPMENT_WALK_FORWARD"
    )
    selection_data_overlap: Literal[True] = True
    statistical_independence: Literal["UNASSESSED"] = "UNASSESSED"
    isolation_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def closed_window_shape(self):
        if (
            self.history_start != self.validation_start - 2
            or self.validation_stop <= self.validation_start
        ):
            raise ValueError("Development window requires two closed bars and nonempty validation")
        return self

    @field_validator("isolation_verified", "promotion_allowed", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("Dataset windows cannot grant authority")
        return value

    @field_validator("selection_data_overlap", mode="before")
    @classmethod
    def explicit_overlap(cls, value):
        if value is not True:
            raise ValueError("Historical development overlaps original candidate design")
        return value

    @property
    def window_ref(self):
        return "candidate-window:" + sha256(self.model_dump_json().encode()).hexdigest()


@dataclass(frozen=True)
class CandidateDatasetView:
    input_package: ValidationPackage
    window: CandidateDatasetWindow
    test_result: CandidateTestResult

    @property
    def package_ref(self):
        return self.input_package.package_ref

    @property
    def candidate(self):
        return self.input_package.candidate

    @property
    def sdk_files(self):
        return self.input_package.sdk_files

    @property
    def plan(self):
        return self.input_package.plan


CandidateDatasetInput = ValidationPackage | CandidateDatasetView


def _checked_package(package):
    if type(package) is not ValidationPackage:
        raise ValueError("Original validation package required")
    return ValidationPackage.model_validate_json(package.model_dump_json())


def _checked_tests(package, result):
    if type(result) is not CandidateTestResult:
        raise ValueError("Checked proposed-test observation required")
    result = CandidateTestResult.model_validate_json(result.model_dump_json())
    expected = candidate_test_request(package)
    if (result.package_ref, result.candidate_ref, result.request_ref, result.status) != (
        package.package_ref,
        package.candidate.candidate_ref,
        expected.request_ref,
        "PROPOSED_TESTS_PASSED_UNATTESTED",
    ):
        raise ValueError("Development window requires exact original passed proposed tests")
    return result


def _window(package, fold_index, test_result):
    if type(fold_index) is not int or not 0 <= fold_index <= 2047:
        raise ValueError("Explicit bounded development fold required")
    dataset = parse_research_csv(base64.b64decode(package.training.raw_base64, validate=True))
    start = package.plan.walk_forward_train_bars + fold_index * package.plan.walk_forward_test_bars
    planned_stop = start + package.plan.walk_forward_test_bars
    if start >= len(dataset.bars):
        raise ValueError("Declared development fold does not exist")
    stop = min(planned_stop, len(dataset.bars))
    selected = BarDataset(dataset.bars[start - 2 : stop])
    with localcontext(Context(prec=28)):
        fingerprint = selected.fingerprint
    return CandidateDatasetWindow(
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        test_result_sha256=sha256(test_result.model_dump_json().encode()).hexdigest(),
        dataset_ref=package.training.receipt.dataset_ref,
        original_dataset_fingerprint=package.training.receipt.dataset_fingerprint,
        window_fingerprint=fingerprint,
        fold_index=fold_index,
        history_start=start - 2,
        validation_start=start,
        validation_stop=stop,
        complete_declared_window=stop == planned_stop,
    ), selected


def development_window(package, fold_index, test_result):
    package = _checked_package(package)
    result = _checked_tests(package, test_result)
    window, _ = _window(package, fold_index, result)
    return CandidateDatasetView(package, window, result)


def candidate_dataset_inputs(context):
    if type(context) is ValidationPackage:
        package = _checked_package(context)
        dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64, validate=True))
        return package, package.holdout.receipt, dataset
    if type(context) is not CandidateDatasetView:
        raise ValueError("Checked original package or explicit dataset view required")
    package = _checked_package(context.input_package)
    result = _checked_tests(package, context.test_result)
    window = CandidateDatasetWindow.model_validate_json(context.window.model_dump_json())
    expected, dataset = _window(package, window.fold_index, result)
    if window != expected:
        raise ValueError("Dataset window differs from original source/test/data/plan bindings")
    return CandidateDatasetView(package, expected, result), package.training.receipt, dataset
