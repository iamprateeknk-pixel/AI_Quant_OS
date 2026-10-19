import pytest

from ai_quant_os.agent_lifecycle_store import AgentLifecycleStore
from ai_quant_os.agent_registry import AgentRegistry, load_agent_catalog


def test_lifecycle_transitions_restore_by_exact_manifest_and_new_versions_stay_shadow(tmp_path):
    path = tmp_path / "agent-lifecycle.sqlite3"
    first = load_agent_catalog()
    first.bind_lifecycle_store(AgentLifecycleStore(path))
    first.set_lifecycle("A02", "0.2.0", "LIMITED")
    first.set_lifecycle("A02", "0.2.0", "ACTIVE")

    restored = load_agent_catalog()
    restored.bind_lifecycle_store(AgentLifecycleStore(path))
    assert restored.lifecycle("A02", "0.2.0") == "ACTIVE"
    assert restored.lifecycle("A02", "0.1.0") == "SHADOW"
    new_version = restored.get("A02", "0.2.0").model_copy(update={"agent_version": "9.9.9"})
    restored.register(new_version)
    assert restored.lifecycle("A02", "9.9.9") == "SHADOW"


def test_lifecycle_journal_tampering_refuses_restore(tmp_path):
    path = tmp_path / "agent-lifecycle.sqlite3"
    registry = load_agent_catalog()
    registry.bind_lifecycle_store(AgentLifecycleStore(path))
    registry.set_lifecycle("A02", "0.2.0", "LIMITED")

    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE agent_lifecycle_events SET event_json = '{}' WHERE rowid = 1")

    with pytest.raises(ValueError, match="integrity|canonical|audit|binding"):
        load_agent_catalog().bind_lifecycle_store(AgentLifecycleStore(path))


def test_lifecycle_restore_refuses_changed_manifest_pin(tmp_path):
    path = tmp_path / "agent-lifecycle.sqlite3"
    original = load_agent_catalog()
    original.bind_lifecycle_store(AgentLifecycleStore(path))
    original.set_lifecycle("A02", "0.2.0", "LIMITED")

    changed = AgentRegistry()
    manifest = original.get("A02", "0.2.0").model_copy(update={"name": "substituted"})
    changed.register(manifest)
    with pytest.raises(ValueError, match="manifest pin"):
        changed.bind_lifecycle_store(AgentLifecycleStore(path))


def test_quarantine_transition_is_durable_and_remains_pinned_after_restart(tmp_path):
    path = tmp_path / "agent-lifecycle.sqlite3"
    first = load_agent_catalog()
    first.bind_lifecycle_store(AgentLifecycleStore(path))
    first.set_lifecycle("A02", "0.2.0", "QUARANTINED")

    restored = load_agent_catalog()
    restored.bind_lifecycle_store(AgentLifecycleStore(path))
    assert restored.lifecycle("A02", "0.2.0") == "QUARANTINED"
    with pytest.raises(PermissionError, match="independently gated recovery"):
        restored.set_lifecycle("A02", "0.2.0", "ACTIVE")
