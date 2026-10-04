"""Bounded per-decision inputs; source text and targets are inert and unattested."""

from decimal import Context, Decimal, localcontext
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.market_data import Bar, BarDataset
from ai_quant_os.strategy_candidate_contracts import ProposedFile
from ai_quant_os.validation_package_contracts import SDKFile, SDK_PATHS, ValidationPackage


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate invocation JSON key")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("Non-finite JSON constant")


def _wire_object(raw: bytes, maximum: int) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ValueError("Invocation wire byte bound/type mismatch")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_no_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid invocation JSON") from error
    if type(value) is not dict:
        raise ValueError("Invocation wire must be an object")
    return value


class ClosedBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    observed_at: AwareDatetime
    open: Decimal = Field(gt=0, allow_inf_nan=False)
    high: Decimal = Field(gt=0, allow_inf_nan=False)
    low: Decimal = Field(gt=0, allow_inf_nan=False)
    close: Decimal = Field(gt=0, allow_inf_nan=False)
    volume: Decimal = Field(ge=0, allow_inf_nan=False)

    def to_bar(self) -> Bar:
        return Bar(
            self.instrument_id,
            self.observed_at,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )

    @model_validator(mode="after")
    def checked_bar(self):
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("Closed bar clock must be UTC")
        self.to_bar()
        return self


class CandidateInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    package_ref: str = Field(pattern=r"^validation-package:[a-f0-9]{64}$")
    candidate_ref: str = Field(pattern=r"^strategy-candidate:[a-f0-9]{64}$")
    index: int = Field(strict=True, ge=2, le=4095)
    execute_at: AwareDatetime
    instrument_id: Literal["EURUSD", "GBPUSD", "USDJPY", "EURJPY"]
    source: ProposedFile
    sdk_files: tuple[SDKFile, ...] = Field(min_length=4, max_length=4)
    history: tuple[ClosedBar, ...] = Field(min_length=2, max_length=2)
    history_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["WAIT_FOR_CONFINED_PREFIX_WORKER"] = "WAIT_FOR_CONFINED_PREFIX_WORKER"
    isolation_verified: Literal[False] = False
    promotion_authorized: Literal[False] = False

    @model_validator(mode="after")
    def prefix_identity(self):
        if self.execute_at.utcoffset().total_seconds() != 0:
            raise ValueError("Invocation clock must be UTC")
        if (
            self.source.path != "candidate.py"
            or tuple(row.path for row in self.sdk_files) != SDK_PATHS
        ):
            raise ValueError("Invocation source/SDK inventory mismatch")
        prior, latest = self.history
        if not prior.observed_at < latest.observed_at < self.execute_at or any(
            row.instrument_id != self.instrument_id for row in self.history
        ):
            raise ValueError("Invocation history must precede its open and match the instrument")
        with localcontext(Context(prec=28)):
            fingerprint = BarDataset(row.to_bar() for row in self.history).fingerprint
        if fingerprint != self.history_sha256:
            raise ValueError("Invocation history fingerprint mismatch")
        if len(self.model_dump_json().encode()) > 524288:
            raise ValueError("Invocation exceeds wire byte bound")
        return self

    @classmethod
    def from_wire(cls, raw: bytes) -> "CandidateInvocation":
        return cls.model_validate(_wire_object(raw, 524288))

    @property
    def invocation_ref(self) -> str:
        return "candidate-invocation:" + sha256(self.model_dump_json().encode()).hexdigest()

    def require_package(self, package: ValidationPackage) -> None:
        from ai_quant_os.candidate_invocations import prepare_invocation

        checked = CandidateInvocation.model_validate_json(self.model_dump_json())
        expected = prepare_invocation(package, checked.index)
        if checked != expected:
            raise ValueError("Invocation differs from exact package prefix/source/SDK")


class CandidateTargetOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    invocation_ref: str = Field(pattern=r"^candidate-invocation:[a-f0-9]{64}$")
    target: int = Field(strict=True, ge=-1, le=1)
    status: Literal["UNATTESTED_TARGET_OUTPUT"] = "UNATTESTED_TARGET_OUTPUT"
    isolation_verified: Literal[False] = False
    promotion_authorized: Literal[False] = False

    @classmethod
    def from_wire(cls, raw: bytes) -> "CandidateTargetOutput":
        return cls.model_validate(_wire_object(raw, 2048))
