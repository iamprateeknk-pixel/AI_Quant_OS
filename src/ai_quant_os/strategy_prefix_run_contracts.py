"""Pinned full-prefix run declarations and unattested replay results."""

from hashlib import sha256
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.strategy_execution_tape_contracts import StrategyExecutionReplayReport
from ai_quant_os.strategy_image_runtime import StrategyImageHandoff
from ai_quant_os.strategy_prefix_execution_contracts import StrategyPrefixExecutionReceipt
from ai_quant_os.strategy_prefix_release_contracts import PrefixReleaseDirectory


class StrategyRunImageInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    archive: str = Field(min_length=1, max_length=4096)
    base_metadata: str = Field(min_length=1, max_length=4096)
    base_manifest_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    worker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    config_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    def arguments(self) -> dict:
        result = self.model_dump()
        result["archive"] = Path(self.archive)
        result["base_metadata"] = Path(self.base_metadata)
        return result


class StrategyPrefixRunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    row_count: int = Field(strict=True, ge=2, le=4096)
    parents: tuple[PrefixReleaseDirectory, ...] = Field(min_length=1, max_length=128)
    image_inputs: StrategyRunImageInputs
    handoff: StrategyImageHandoff
    quantity: int = Field(default=1, strict=True, ge=1, le=500000)
    initial_balance: Decimal = Field(
        default=Decimal("100000"), gt=0, le=Decimal("1000000000000"), allow_inf_nan=False
    )
    maximum_journal_bytes: Literal[134217728] = 134217728
    maximum_recovery_bytes: Literal[8589934592] = 8589934592
    state: Literal["PREFIX_RUN_PREPARED_NOT_EXECUTION_ATTESTED"] = (
        "PREFIX_RUN_PREPARED_NOT_EXECUTION_ATTESTED"
    )
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def image_binding(self):
        archive = self.handoff.archive
        pins = self.image_inputs
        if any(
            getattr(pins, name) != getattr(archive, name)
            for name in (
                "base_manifest_digest",
                "worker_sha256",
                "archive_sha256",
                "manifest_digest",
                "config_digest",
            )
        ):
            raise ValueError("Run image inputs differ from independently verified handoff")
        return self

    @property
    def run_sha256(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class StrategyRunPrefix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: int = Field(strict=True, ge=2, le=4095)
    job_name: str = Field(pattern=r"^strategy-job-[a-f0-9]{32}$")
    invocation_ref: str = Field(pattern=r"^candidate-invocation:[a-f0-9]{64}$")
    phase: Literal["RESERVED", "STAGED", "COMPLETED"]
    stage_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt: StrategyPrefixExecutionReceipt | None = None
    release_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def phase_binding(self):
        if (
            (self.stage_sha256 is None) != (self.phase == "RESERVED")
            or (self.receipt is not None) != (self.phase == "COMPLETED")
            or (self.release_sha256 is not None) != (self.phase == "COMPLETED")
        ):
            raise ValueError("Run prefix evidence differs from reserved phase")
        if self.receipt is not None:
            receipt = StrategyPrefixExecutionReceipt.model_validate_json(
                self.receipt.model_dump_json()
            )
            if (
                receipt.ownership.job_name,
                receipt.ownership.stage_sha256,
                receipt.target.invocation_ref,
            ) != (self.job_name, self.stage_sha256, self.invocation_ref):
                raise ValueError("Run prefix completion differs from original reservation")
        return self


class StrategyPrefixRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    manifest: StrategyPrefixRunManifest
    replay: StrategyExecutionReplayReport
    stress_replay: StrategyExecutionReplayReport
    status: Literal["UNATTESTED_FULL_PREFIX_RUN_NATIVE_REPLAY"] = (
        "UNATTESTED_FULL_PREFIX_RUN_NATIVE_REPLAY"
    )
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @model_validator(mode="after")
    def inventory_binding(self):
        collection = self.replay.collection
        if (
            self.stress_replay.collection != collection
            or self.replay.replay.cost_scenario != "BASE"
            or self.stress_replay.replay.cost_scenario != "DECLARED_STRESS"
        ):
            raise ValueError("Full run requires matching base and declared-stress inventories")
        if any(
            (row.replay.quantity, row.replay.initial_balance)
            != (self.manifest.quantity, self.manifest.initial_balance)
            for row in (self.replay, self.stress_replay)
        ):
            raise ValueError("Native replay quantity/balance differs from pinned run declaration")
        if (
            collection.package_ref,
            collection.candidate_ref,
            collection.image_id,
            collection.worker_sha256,
            len(collection.receipts),
        ) != (
            self.manifest.package_ref,
            self.manifest.candidate_ref,
            self.manifest.handoff.loaded_image_id,
            self.manifest.image_inputs.worker_sha256,
            self.manifest.row_count - 2,
        ):
            raise ValueError("Full run replay differs from original manifest coverage/pins")
        return self

    @property
    def report_ref(self) -> str:
        return "strategy-prefix-run:" + sha256(self.model_dump_json().encode()).hexdigest()
