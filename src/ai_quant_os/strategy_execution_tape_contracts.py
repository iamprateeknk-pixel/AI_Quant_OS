"""Operator-pinned completion metadata and native numeric replay, without authority."""

from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.candidate_invocations import assemble_unattested_tape
from ai_quant_os.signal_tape_contracts import SignalReplayReport, SignalTape
from ai_quant_os.strategy_prefix_execution_contracts import StrategyPrefixExecutionReceipt
from ai_quant_os.candidate_dataset_views import CandidateDatasetInput


class StrategyExecutionTape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    worker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipts: tuple[StrategyPrefixExecutionReceipt, ...] = Field(max_length=4094)
    tape: SignalTape
    provenance: Literal["OPERATOR_PINNED_UNATTESTED_COMPLETION_RECEIPTS"] = (
        "OPERATOR_PINNED_UNATTESTED_COMPLETION_RECEIPTS"
    )
    evidence_kind: Literal["COMPLETION_METADATA_NOT_DAEMON_ATTESTATION"] = (
        "COMPLETION_METADATA_NOT_DAEMON_ATTESTATION"
    )
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def inventory(self):
        checked = tuple(
            StrategyPrefixExecutionReceipt.model_validate_json(row.model_dump_json())
            for row in self.receipts
        )
        if checked != self.receipts or checked != tuple(
            sorted(checked, key=lambda row: row.target.invocation_ref)
        ):
            raise ValueError("Canonical completion inventory required")
        for identities in (
            [row.ownership.owner for row in checked],
            [
                PureWindowsPath(row.ownership.job_path)
                if PureWindowsPath(row.ownership.job_path).drive
                else PurePosixPath(row.ownership.job_path)
                for row in checked
            ],
            [row.exit.container_id for row in checked],
            [row.target.invocation_ref for row in checked],
        ):
            if len(set(identities)) != len(identities):
                raise ValueError("Completion custody or decision identity was reused")
        if any(
            row.ownership.image_id != self.image_id
            or row.ownership.worker_sha256 != self.worker_sha256
            for row in checked
        ) or (self.tape.package_ref, self.tape.candidate_ref) != (
            self.package_ref,
            self.candidate_ref,
        ):
            raise ValueError("Independent completion image, source, or package pin changed")
        return self

    @property
    def collection_ref(self) -> str:
        return "strategy-execution-tape:" + sha256(self.model_dump_json().encode()).hexdigest()

    def require_package(self, package: CandidateDatasetInput) -> None:
        checked = type(self).model_validate_json(self.model_dump_json())
        expected = assemble_unattested_tape(package, tuple(row.target for row in checked.receipts))
        if checked.tape != expected:
            raise ValueError("Completion inventory does not reproduce the original package tape")


class StrategyExecutionReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    collection: StrategyExecutionTape
    replay: SignalReplayReport
    status: Literal["UNATTESTED_COMPLETION_NATIVE_REPLAY"] = "UNATTESTED_COMPLETION_NATIVE_REPLAY"
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def replay_binding(self):
        if self.replay.tape != self.collection.tape:
            raise ValueError("Native replay must use the exact completion tape")
        return self

    @property
    def report_ref(self) -> str:
        return "strategy-execution-replay:" + sha256(self.model_dump_json().encode()).hexdigest()
