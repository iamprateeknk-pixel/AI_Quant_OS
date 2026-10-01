from dataclasses import replace
import json
from pathlib import Path

import pytest


def genome(parameters=()):
    from ai_quant_os.strategy_contracts import StrategyGenome

    return StrategyGenome("moving-average-cross@1.0.0", "BAR", parameters)


def test_genomes_are_canonical_immutable_and_reject_untyped_parameters():
    from ai_quant_os.strategy_contracts import StrategyGenome

    first = genome((("slow", 5), ("fast", 2)))
    second = genome((("fast", 2), ("slow", 5)))
    assert first == second and first.fingerprint == second.fingerprint
    with pytest.raises(ValueError):
        genome((("fast", True),))
    with pytest.raises(ValueError):
        genome((("fast", 2), ("fast", 3)))
    with pytest.raises(ValueError):
        StrategyGenome("../escape", "BAR", ())


def test_builtin_source_binding_and_version_registration_survive_restart(tmp_path):
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin

    registry = StrategyRegistry(tmp_path)
    plugin, binding = prepare_builtin(genome(), registry)
    assert (plugin.fast, plugin.slow) == (2, 3)
    assert binding.genome.parameters == (("fast", 2), ("slow", 3))
    assert registry.get(binding.implementation.strategy_ref) == binding.implementation
    restarted = StrategyRegistry(tmp_path)
    assert (
        restarted.get(binding.implementation.strategy_ref).fingerprint
        == binding.implementation_sha256
    )
    assert prepare_builtin(genome(), restarted)[1] == binding
    changed = replace(binding.implementation, entry_point="ai_quant_os.strategy_samples:BuyAndHold")
    with pytest.raises(ValueError, match="Immutable"):
        restarted.register(changed)


@pytest.mark.parametrize(
    "requested",
    [
        ("unknown@1.0.0", "BAR", ()),
        ("moving-average-cross@1.0.0", "TICK", ()),
        ("moving-average-cross@1.0.0", "BAR", (("slow", 1),)),
        ("buy-and-hold@1.0.0", "BAR", (("fast", 2),)),
    ],
)
def test_builder_only_accepts_known_compatible_validated_genomes(tmp_path, requested):
    from ai_quant_os.strategy_contracts import StrategyGenome
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin

    with pytest.raises(ValueError):
        prepare_builtin(StrategyGenome(*requested), StrategyRegistry(tmp_path))
    assert not tuple(tmp_path.rglob("*.json"))


def test_modified_source_and_registry_manifest_are_detected(tmp_path):
    from ai_quant_os.strategy_registry import (
        StrategyRegistry,
        prepare_builtin,
        verify_implementation,
    )

    registry = StrategyRegistry(tmp_path / "registry")
    _, binding = prepare_builtin(genome(), registry)
    copied = tmp_path / "source"
    copied.mkdir()
    root = Path(__file__).parents[1] / "src" / "ai_quant_os"
    for row in binding.implementation.source_files:
        (copied / row.path).write_bytes((root / row.path).read_bytes())
    verify_implementation(binding.implementation, copied)
    target = copied / "strategy_samples.py"
    target.write_text(target.read_text() + "\n# changed implementation\n")
    with pytest.raises(ValueError, match="source"):
        verify_implementation(binding.implementation, copied)
    manifest = next((tmp_path / "registry").rglob("*.json"))
    stored = json.loads(manifest.read_text())
    stored["implementation"]["entry_point"] = "different:Entry"
    manifest.write_text(json.dumps(stored))
    with pytest.raises(ValueError):
        registry.get(binding.implementation.strategy_ref)


def test_binding_hashes_and_configuration_identity_cannot_disagree(tmp_path):
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin

    _, binding = prepare_builtin(genome(), StrategyRegistry(tmp_path))
    with pytest.raises(ValueError):
        replace(binding, genome_sha256="0" * 64)
    with pytest.raises(ValueError):
        replace(binding, implementation_sha256="0" * 64)
    with pytest.raises(ValueError):
        replace(binding, schema_version="2.0")


def test_concurrent_same_version_registration_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin

    registry = StrategyRegistry(tmp_path)
    _, binding = prepare_builtin(genome(), registry)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: registry.register(binding.implementation), range(12)))
    assert registry.get(binding.genome.strategy_ref) == binding.implementation
    assert len(tuple(tmp_path.glob("*.json"))) == 1


def test_registry_retains_and_verifies_exact_source_artifacts(tmp_path):
    from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin

    registry = StrategyRegistry(tmp_path)
    _, binding = prepare_builtin(genome(), registry)
    archived = registry.archived_sources(binding.genome.strategy_ref)
    root = Path(__file__).parents[1] / "src" / "ai_quant_os"
    assert set(archived) == {row.path for row in binding.implementation.source_files}
    assert archived["strategy_samples.py"] == (root / "strategy_samples.py").read_text().encode()
    stored = next((tmp_path / "objects").rglob("*.py"))
    stored.write_bytes(b"corrupt source")
    with pytest.raises(ValueError, match="source"):
        registry.get(binding.genome.strategy_ref)


def test_registered_native_run_binds_configuration_and_cli_report(tmp_path):
    pytest.importorskip("nautilus_trader")
    from decimal import Decimal
    import subprocess
    import sys
    from ai_quant_os.backtest import BacktestCosts
    from ai_quant_os.market_data import import_bars_csv
    from ai_quant_os.registered_backtest import run_registered_backtest
    from ai_quant_os.strategy_registry import StrategyRegistry

    source = Path(__file__).parent / "fixtures" / "nautilus_replay.csv"
    dataset = import_bars_csv(source)
    run = run_registered_backtest(
        dataset,
        genome((("fast", 1), ("slow", 2))),
        BacktestCosts(Decimal(0), Decimal(0)),
        StrategyRegistry(tmp_path / "registry"),
    )
    assert run.binding.genome.parameters == (("fast", 1), ("slow", 2))
    assert run.result.strategy_ref == run.binding.genome.strategy_ref
    assert run == run_registered_backtest(
        dataset,
        genome((("slow", 2), ("fast", 1))),
        BacktestCosts(Decimal(0), Decimal(0)),
        StrategyRegistry(tmp_path / "registry"),
    )
    output = tmp_path / "result.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.nautilus_cli",
            str(source),
            "--fast-window",
            "1",
            "--slow-window",
            "2",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    binding = json.loads(output.read_text())["strategy_binding"]
    assert binding["genome_sha256"] == run.binding.genome_sha256
    assert binding["implementation_sha256"] == run.binding.implementation_sha256
