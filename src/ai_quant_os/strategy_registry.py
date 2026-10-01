"""Checked built-in construction and immutable local implementation registration."""

from hashlib import sha256
import json
from pathlib import Path

from ai_quant_os.data_archive import _write_immutable
from ai_quant_os.strategy_contracts import (
    SourceFingerprint,
    StrategyBinding,
    StrategyGenome,
    StrategyImplementation,
)
from ai_quant_os.strategy_samples import BuyAndHold, MovingAverageCrossover
from ai_quant_os.tick_strategy import TickMomentum
from ai_quant_os.directional_strategy import DirectionalMovingAverage

PACKAGE_ROOT = Path(__file__).resolve().parent
_BUILDERS = {
    "ai_quant_os.strategy_samples:BuyAndHold": BuyAndHold,
    "ai_quant_os.strategy_samples:MovingAverageCrossover": MovingAverageCrossover,
    "ai_quant_os.tick_strategy:TickMomentum": TickMomentum,
    "ai_quant_os.directional_strategy:DirectionalMovingAverage": DirectionalMovingAverage,
}


def _decode(record) -> StrategyImplementation:
    try:
        return StrategyImplementation(
            **{
                **record,
                "parameter_defaults": tuple(tuple(row) for row in record["parameter_defaults"]),
                "source_files": tuple(SourceFingerprint(**row) for row in record["source_files"]),
            }
        )
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError("Invalid implementation manifest") from error


def verify_implementation(implementation: StrategyImplementation, source_root=PACKAGE_ROOT) -> None:
    for row in implementation.source_files:
        # Normalize only universal line endings, so Git checkout conversion is immaterial.
        raw = (source_root / row.path).read_text(encoding="utf-8").encode("utf-8")
        if sha256(raw).hexdigest() != row.sha256:
            raise ValueError("Strategy source does not match registered implementation")


class StrategyRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, strategy_ref):
        StrategyGenome(strategy_ref, "BAR")
        return self.root / (strategy_ref + ".json")

    def register(self, implementation: StrategyImplementation) -> None:
        from dataclasses import asdict

        verify_implementation(implementation)
        for row in implementation.source_files:
            content = (PACKAGE_ROOT / row.path).read_text(encoding="utf-8").encode("utf-8")
            if sha256(content).hexdigest() != row.sha256:
                raise ValueError("Strategy source changed during registration")
            _write_immutable(self.root / "objects" / (row.sha256 + ".py"), content)

        raw = json.dumps(
            {"implementation": asdict(implementation), "sha256": implementation.fingerprint},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        _write_immutable(self._path(implementation.strategy_ref), raw)

    def get(self, strategy_ref: str) -> StrategyImplementation:
        path = self._path(strategy_ref)
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("Strategy manifest exceeds size limit")
        record = json.loads(raw)
        if type(record) is not dict or set(record) != {"implementation", "sha256"}:
            raise ValueError("Invalid registry envelope")
        implementation = _decode(record["implementation"])
        if (
            implementation.strategy_ref != strategy_ref
            or implementation.fingerprint != record["sha256"]
        ):
            raise ValueError("Strategy registry fingerprint or identity mismatch")
        self._read_sources(implementation)
        return implementation

    def _read_sources(self, implementation):
        sources = {}
        for row in implementation.source_files:
            with (self.root / "objects" / (row.sha256 + ".py")).open("rb") as source:
                raw = source.read(1048577)
            if len(raw) > 1048576 or sha256(raw).hexdigest() != row.sha256:
                raise ValueError("Archived strategy source fingerprint mismatch")
            sources[row.path] = raw
        return sources

    def archived_sources(self, strategy_ref: str) -> dict[str, bytes]:
        return self._read_sources(self.get(strategy_ref))


def prepare_builtin(genome: StrategyGenome, registry: StrategyRegistry):
    records = json.loads((PACKAGE_ROOT / "strategy_manifests.json").read_text(encoding="utf-8"))
    manifests = tuple(_decode(record) for record in records)
    if len({row.strategy_ref for row in manifests}) != len(manifests):
        raise ValueError("Duplicate built-in implementation version")
    implementation = next(
        (row for row in manifests if row.strategy_ref == genome.strategy_ref), None
    )
    if implementation is None or implementation.input_kind != genome.input_kind:
        raise ValueError("Unknown or incompatible built-in strategy")
    defaults = dict(implementation.parameter_defaults)
    if set(dict(genome.parameters)) - set(defaults):
        raise ValueError("Unsupported strategy parameters")
    parameters = {**defaults, **dict(genome.parameters)}
    resolved = StrategyGenome(genome.strategy_ref, genome.input_kind, tuple(parameters.items()))
    builder = _BUILDERS.get(implementation.entry_point)
    if builder is None:
        raise ValueError("Unregistered built-in entry point")
    verify_implementation(implementation)
    plugin = builder(**parameters)
    registry.register(implementation)
    if registry.get(implementation.strategy_ref) != implementation:
        raise ValueError("Registered implementation differs from built-in source")
    return plugin, StrategyBinding(
        "1.0",
        "TRUSTED_BUILTIN_SOURCE_BINDING",
        resolved,
        resolved.fingerprint,
        implementation,
        implementation.fingerprint,
    )
