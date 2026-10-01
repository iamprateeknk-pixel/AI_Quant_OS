"""Immutable strategy configurations and source-provenance contracts."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Literal


def canonical_bytes(value) -> bytes:
    return json.dumps(asdict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _reference(value):
    if (
        not isinstance(value, str)
        or len(value) > 128
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+", value)
    ):
        raise ValueError("Invalid versioned strategy reference")


def _parameters(value):
    if type(value) is not tuple or len(value) > 16:
        raise ValueError("Parameters must be a bounded immutable tuple")
    for row in value:
        if (
            type(row) is not tuple
            or len(row) != 2
            or not isinstance(row[0], str)
            or not re.fullmatch(r"[a-z][a-z_]{0,31}", row[0])
            or type(row[1]) is not int
            or not 0 <= row[1] <= 4096
        ):
            raise ValueError("Invalid integer strategy parameter")
    if len({name for name, _ in value}) != len(value):
        raise ValueError("Duplicate strategy parameter")
    return tuple(sorted(value))


@dataclass(frozen=True)
class StrategyGenome:
    strategy_ref: str
    input_kind: Literal["BAR", "TICK"]
    parameters: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        _reference(self.strategy_ref)
        if self.input_kind not in ("BAR", "TICK"):
            raise ValueError("Unknown strategy input kind")
        object.__setattr__(self, "parameters", _parameters(self.parameters))

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_bytes(self)).hexdigest()


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    sha256: str

    def __post_init__(self):
        if not isinstance(self.path, str) or not re.fullmatch(r"[a-z][a-z0-9_]*\.py", self.path):
            raise ValueError("Invalid package source path")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid source fingerprint")


@dataclass(frozen=True)
class StrategyImplementation:
    strategy_ref: str
    input_kind: Literal["BAR", "TICK"]
    entry_point: str
    parameter_defaults: tuple[tuple[str, int], ...]
    source_files: tuple[SourceFingerprint, ...]

    def __post_init__(self):
        StrategyGenome(self.strategy_ref, self.input_kind, self.parameter_defaults)
        object.__setattr__(self, "parameter_defaults", _parameters(self.parameter_defaults))
        if not isinstance(self.entry_point, str) or not re.fullmatch(
            r"ai_quant_os\.[a-z_]+:[A-Za-z][A-Za-z0-9]+", self.entry_point
        ):
            raise ValueError("Invalid built-in entry point")
        if (
            type(self.source_files) is not tuple
            or not 1 <= len(self.source_files) <= 16
            or any(type(row) is not SourceFingerprint for row in self.source_files)
            or len({row.path for row in self.source_files}) != len(self.source_files)
        ):
            raise ValueError("Invalid immutable source inventory")
        object.__setattr__(
            self, "source_files", tuple(sorted(self.source_files, key=lambda row: row.path))
        )

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_bytes(self)).hexdigest()


@dataclass(frozen=True)
class StrategyBinding:
    schema_version: Literal["1.0"]
    provenance: Literal["TRUSTED_BUILTIN_SOURCE_BINDING"]
    genome: StrategyGenome
    genome_sha256: str
    implementation: StrategyImplementation
    implementation_sha256: str

    def __post_init__(self):
        if (
            self.schema_version != "1.0"
            or self.provenance != "TRUSTED_BUILTIN_SOURCE_BINDING"
            or self.genome_sha256 != self.genome.fingerprint
            or self.implementation_sha256 != self.implementation.fingerprint
            or self.genome.strategy_ref != self.implementation.strategy_ref
            or self.genome.input_kind != self.implementation.input_kind
            or {name for name, _ in self.genome.parameters}
            != {name for name, _ in self.implementation.parameter_defaults}
        ):
            raise ValueError("Strategy binding identity or fingerprint mismatch")
