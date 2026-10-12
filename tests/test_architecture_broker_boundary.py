"""Prevent agent and strategy source from importing broker execution surfaces."""

from __future__ import annotations

import ast
import json
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
AGENT_AND_STRATEGY_SOURCES = tuple(
    path
    for path in PACKAGE.rglob("*.py")
    if path.name != "__init__.py" and ("agent" in path.stem or "strategy" in path.stem)
)
ARCHITECTURE = ROOT / ".clean" / "architecture.json"
DOMAIN_SOURCES = (
    PACKAGE / "contracts.py",
    PACKAGE / "market.py",
    PACKAGE / "portfolio.py",
    PACKAGE / "risk.py",
    PACKAGE / "backtest.py",
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
            if is_importlib or is_dynamic_import:
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    found.add("<dynamic import>")
                    continue
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
    assert _broker_imports(
        'import importlib\nimportlib.import_module("ai_quant_os." + "broker")'
    ) == ["<dynamic import>"]
    assert _broker_imports("__import__(module_name)") == ["<dynamic import>"]


def test_agent_and_strategy_source_inventory_includes_strategy_implementations():
    assert PACKAGE / "strategy_builder.py" in AGENT_AND_STRATEGY_SOURCES
    assert PACKAGE / "strategy_contracts.py" in AGENT_AND_STRATEGY_SOURCES
    assert PACKAGE / "strategy_prefix_execution.py" in AGENT_AND_STRATEGY_SOURCES
    assert PACKAGE / "strategy_image_runtime.py" in AGENT_AND_STRATEGY_SOURCES


def test_agent_and_strategy_sources_do_not_import_broker_execution_surfaces():
    violations = {
        path.relative_to(ROOT).as_posix(): _broker_imports(path.read_text(encoding="utf-8"))
        for path in AGENT_AND_STRATEGY_SOURCES
        if path.is_file()
    }
    violations = {path: imports for path, imports in violations.items() if imports}
    assert not violations, f"Agent or strategy source crosses broker boundary: {violations}"


def _import_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    importlib_names: set[str] = set()
    import_module_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "importlib"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Call):
            is_importlib = isinstance(node.func, ast.Attribute) and (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_names
                and node.func.attr == "import_module"
            )
            is_dynamic = isinstance(node.func, ast.Name) and (
                node.func.id == "__import__" or node.func.id in import_module_names
            )
            if is_importlib or is_dynamic:
                value = node.args[0] if node.args else None
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    names.add(value.value)
                else:
                    names.add("<dynamic import>")
    return names


def test_machine_readable_architecture_domain_boundary_is_enforced():
    rules = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    assert rules["schema_version"] == "1.0"
    domain = rules["layers"]["domain"]
    assert {ROOT / path for path in domain["modules"]} == set(DOMAIN_SOURCES)
    forbidden = tuple(domain["forbidden_import_prefixes"])
    violations = {}
    for path in DOMAIN_SOURCES:
        imports = _import_names(ast.parse(path.read_text(encoding="utf-8")))
        blocked = sorted(
            name
            for name in imports
            if name == "<dynamic import>"
            or any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
        )
        if blocked:
            violations[path.relative_to(ROOT).as_posix()] = blocked
    assert not violations, f"Domain imports cross declared architecture boundary: {violations}"


def test_machine_readable_architecture_boundary_rejects_infrastructure_imports():
    rules = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    forbidden = tuple(rules["layers"]["domain"]["forbidden_import_prefixes"])
    source = ast.parse("import pydantic\nfrom ai_quant_os.execution import submit")
    blocked = sorted(
        name
        for name in _import_names(source)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    )
    assert blocked == ["ai_quant_os.execution", "pydantic"]
    dynamic = ast.parse('from importlib import import_module as load\nload("pydantic")')
    assert "pydantic" in _import_names(dynamic)
    constructed = ast.parse(
        'from importlib import import_module as load\nload("pydantic" + ".BaseModel")'
    )
    assert "<dynamic import>" in _import_names(constructed)


def test_package_python_modules_are_explicitly_in_architecture_inventory():
    rules = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    inventory = rules["package_module_inventory"]
    declared = set(inventory)
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path in PACKAGE.rglob("*.py")
        if path.name != "__init__.py" and "__pycache__" not in path.parts
    }
    assert len(inventory) == len(declared), "Architecture inventory contains duplicates"
    assert declared == discovered, (
        f"Architecture inventory mismatch; missing={sorted(discovered - declared)}, "
        f"stale={sorted(declared - discovered)}"
    )
