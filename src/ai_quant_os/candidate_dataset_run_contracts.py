"""Separate run manifests pin the explicit training window and its test observation."""

from typing import Literal

from pydantic import model_validator

from ai_quant_os.candidate_dataset_views import CandidateDatasetView, CandidateDatasetWindow
from ai_quant_os.strategy_prefix_run_contracts import (
    StrategyPrefixRunManifest,
    StrategyPrefixRunReport,
)
from ai_quant_os.validation_package_contracts import ValidationPackage


class CandidateDatasetRunManifest(StrategyPrefixRunManifest):
    dataset_window: CandidateDatasetWindow
    state: Literal["DEVELOPMENT_WINDOW_RUN_PREPARED_UNATTESTED"] = (
        "DEVELOPMENT_WINDOW_RUN_PREPARED_UNATTESTED"
    )

    @model_validator(mode="after")
    def window_binding(self):
        window = self.dataset_window
        if (
            (self.package_ref, self.candidate_ref) != (window.package_ref, window.candidate_ref)
            or self.row_count != window.validation_stop - window.history_start
            or window.history_start != window.validation_start - 2
            or window.validation_stop <= window.validation_start
        ):
            raise ValueError("Development run differs from its exact source/data window")
        return self


class CandidateDatasetRunReport(StrategyPrefixRunReport):
    manifest: CandidateDatasetRunManifest
    status: Literal["UNATTESTED_DEVELOPMENT_WINDOW_NATIVE_REPLAY"] = (
        "UNATTESTED_DEVELOPMENT_WINDOW_NATIVE_REPLAY"
    )

    @model_validator(mode="after")
    def dataset_binding(self):
        tape = self.replay.collection.tape
        window = self.manifest.dataset_window
        if (tape.dataset_ref, tape.dataset_fingerprint) != (
            window.dataset_ref,
            window.window_fingerprint,
        ):
            raise ValueError("Development replay differs from its pinned window")
        return self


def run_manifest_model(context):
    if type(context) is CandidateDatasetView:
        return CandidateDatasetRunManifest
    if type(context) is ValidationPackage:
        return StrategyPrefixRunManifest
    raise ValueError("Exact original package or checked dataset view required")


def run_report_model(context):
    if type(context) is CandidateDatasetView:
        return CandidateDatasetRunReport
    if type(context) is ValidationPackage:
        return StrategyPrefixRunReport
    raise ValueError("Exact original package or checked dataset view required")
