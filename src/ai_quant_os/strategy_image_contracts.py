"""Static worker archive identity, never image execution or promotion authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Literal[
        "worker.py", "base_image.json", "base_index.json", "base_manifest.json", "base_config.json"
    ]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StrategyWorkerImageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["OCI_ARCHIVE_ONLY", "ARCHIVE_HASHES_VERIFIED"] = "OCI_ARCHIVE_ONLY"
    platform: Literal["linux/amd64"] = "linux/amd64"
    python_version: Literal["3.11.17"] = "3.11.17"
    base_manifest_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    worker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_pins: tuple[ImageSourcePin, ...] = Field(min_length=5, max_length=5)
    manifest_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    config_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    worker_layer_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_bytes: int = Field(strict=True, gt=0, le=134217728)
    archive_integrity_verified: bool = Field(default=False, strict=True)
    image_verified: Literal[False] = False
    execution_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False
    source_execution_status: Literal["NOT_RUN"] = "NOT_RUN"

    @model_validator(mode="after")
    def checked_receipt(self):
        if (
            tuple(row.path for row in self.source_pins)
            != (
                "worker.py",
                "base_image.json",
                "base_index.json",
                "base_manifest.json",
                "base_config.json",
            )
            or self.source_pins[0].sha256 != self.worker_sha256
        ):
            raise ValueError("Worker image source inventory mismatch")
        if self.archive_integrity_verified != (self.state == "ARCHIVE_HASHES_VERIFIED"):
            raise ValueError("Worker archive verification state mismatch")
        return self
