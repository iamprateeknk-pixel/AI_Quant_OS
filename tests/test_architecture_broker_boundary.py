"""Prevent agent and strategy source from importing broker execution surfaces."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "ai_quant_os"
BROKER_EXECUTION_MODULES = (
    "MetaTrader5",
    "metatrader5",
    "ai_quant_os.broker",
    "ai_quant_os.execution",
    "ai_quant_os.mt5_demo_gateway",
    "ai_quant_os.mt5_demo_worker",
    "ai_quant_os.mt5_sync_service",
)
AGENT_AND_STRATEGY_SOURCES = (
    *PACKAGE.glob("*_agent.py"),
    PACKAGE / "strategy_samples.py",
    PACKAGE / "directional_strategy.py",
    PACKAGE / "tick_strategy.py",
)


def _module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if not node.module:
            return []
        return [
            f"{node.module}.{alias.name}" if node.module == "ai_quant_os" else node.module
            for alias in node.names
        ]
    return []


def _broker_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    importlib_names: set[str] = set()
    import_module_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "importlib"
            )
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )
    for node in ast.walk(tree):
        for module in _module_names(node):
            if any(
                module == boundary or module.startswith(f"{boundary}.")
                for boundary in BROKER_EXECUTION_MODULES
            ):
                found.add(module)
        if isinstance(node, ast.Call):
            is_importlib = isinstance(node.func, ast.Attribute) and (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_names
                and node.func.attr == "import_module"
            )
            is_dynamic_import = isinstance(node.func, ast.Name) and (
                node.func.id == "__import__" or node.func.id in import_module_names
            )
            if (
                (is_importlib or is_dynamic_import)
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                imported = node.args[0].value
                if isinstance(imported, str) and any(
                    imported == boundary or imported.startswith(f"{boundary}.")
                    for boundary in BROKER_EXECUTION_MODULES
                ):
                    found.add(imported)
    return sorted(found)


def test_boundary_detector_catches_static_and_dynamic_broker_imports():
    assert _broker_imports("from ai_quant_os.broker import BrokerRouter") == ["ai_quant_os.broker"]
    assert _broker_imports("from ai_quant_os import broker") == ["ai_quant_os.broker"]
    assert _broker_imports("import MetaTrader5 as mt5") == ["MetaTrader5"]
    assert _broker_imports(
        'import importlib\nimportlib.import_module("ai_quant_os.mt5_demo_worker")'
    ) == ["ai_quant_os.mt5_demo_worker"]
    assert _broker_imports(
        'import importlib as loader\nloader.import_module("ai_quant_os.broker")'
    ) == ["ai_quant_os.broker"]
    assert _broker_imports(
        'from importlib import import_module as load\nload("ai_quant_os.execution")'
    ) == ["ai_quant_os.execution"]
    assert _broker_imports("from ai_quant_os.execution import submit") == ["ai_quant_os.execution"]
    assert _broker_imports('__import__("ai_quant_os.execution")') == ["ai_quant_os.execution"]


def test_agent_and_strategy_sources_do_not_import_broker_execution_surfaces():
    violations = {
        path.relative_to(ROOT).as_posix(): _broker_imports(path.read_text(encoding="utf-8"))
        for path in AGENT_AND_STRATEGY_SOURCES
        if path.is_file()
    }
    violations = {path: imports for path, imports in violations.items() if imports}
    assert not violations, f"Agent or strategy source crosses broker boundary: {violations}"
