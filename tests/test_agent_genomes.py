"""Archival genome tests; retained challenger source is never imported or executed."""

import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from ai_quant_os.agent_registry import load_agent_catalog


def module():
    assert importlib.util.find_spec("ai_quant_os.agent_genomes"), "Missing immutable agent genomes"
    return importlib.import_module("ai_quant_os.agent_genomes")


def objective():
    return module().EvaluationObjective(
        metric="CORRECTNESS",
        direction="MAXIMIZE",
        minimum_improvement="0.01",
        protocol_sha256="a" * 64,
    )


def baseline(agent_id="A02"):
    return module().capture_baseline_genome(load_agent_catalog(), agent_id, objective())


def store(path):
    assert importlib.util.find_spec("ai_quant_os.genome_store"), "Missing durable genome lineage"
    return importlib.import_module("ai_quant_os.genome_store").GenomeStore(
        path, registry=load_agent_catalog()
    )


def child(parent, *, version="0.3.0"):
    payload = parent.model_dump(mode="json")
    payload["parent_ref"] = parent.genome_ref
    payload["manifest"]["agent_version"] = version
    payload["sources"][0]["content"] += (
        "\nraise RuntimeError('challenger must never run on host')\n"
    )
    return module().AgentGenome.model_validate(payload)


def test_baseline_retains_actual_manifest_and_owned_source_without_granting_authority():
    genome = baseline()
    assert genome.manifest == load_agent_catalog().get("A02", "0.2.0")
    assert {row.path for row in genome.sources} == {
        "src/ai_quant_os/regime_agent.py",
        "src/ai_quant_os/regime_features.py",
    }
    assert all(row.content and len(row.sha256) == 64 for row in genome.sources)
    assert genome.parent_ref is None
    assert genome.state == "ARCHIVED_BASELINE"
    assert genome.execution_verified is False
    assert genome.promotion_allowed is False
    assert genome.genome_ref == baseline().genome_ref


def test_root_child_and_grandchild_survive_restart_in_exact_lineage(tmp_path):
    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())
    candidate = child(root)
    grandchild = child(candidate, version="0.4.0")
    assert registry.register(candidate) == candidate
    assert registry.register(grandchild) == grandchild
    reopened = store(tmp_path / "genomes.sqlite3")
    assert reopened.lineage(grandchild.genome_ref) == (root, candidate, grandchild)
    assert reopened.get(candidate.genome_ref).state == "UNVERIFIED_CHALLENGER"
    assert reopened.seed("A02", objective()) == root
    assert len(reopened.list()) == 3


def test_same_version_is_immutable_even_when_changed_content_has_a_valid_new_hash(tmp_path):
    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())
    candidate = child(root)
    registry.register(candidate)
    changed = candidate.model_dump(mode="json")
    changed["sources"][0]["content"] += "\n# different\n"
    with pytest.raises(ValueError, match="version"):
        registry.register(module().AgentGenome.model_validate(changed))
    assert registry.get(candidate.genome_ref) == candidate
    assert len(registry.list()) == 2


@pytest.mark.parametrize("damage", ["risk", "extra", "missing", "duplicate", "unsorted"])
def test_source_inventory_cannot_edit_hard_modules_or_escape_its_owned_boundary(damage):
    payload = baseline().model_dump(mode="json")
    rows = payload["sources"]
    if damage == "risk":
        rows[0]["path"] = "src/ai_quant_os/risk.py"
    elif damage == "extra":
        rows.append({"path": "src/ai_quant_os/execution.py", "content": "# forbidden"})
    elif damage == "missing":
        rows.pop()
    elif damage == "duplicate":
        rows.append(rows[0])
    else:
        rows.reverse()
    with pytest.raises(ValueError):
        module().AgentGenome.model_validate(payload)


@pytest.mark.parametrize(
    "damage",
    [
        "unknown_parent",
        "wrong_agent",
        "same_version",
        "older_version",
        "objective",
        "mission",
        "timeout",
        "no_change",
    ],
)
def test_challenger_requires_retained_parent_and_unchanged_policy(tmp_path, damage):
    registry = store(tmp_path / "genomes.sqlite3")
    parent = registry.seed("A02", objective())
    payload = child(parent).model_dump(mode="json")
    if damage == "unknown_parent":
        payload["parent_ref"] = "agent-genome:" + "0" * 64
    elif damage == "wrong_agent":
        foreign = registry.seed("A07", objective())
        payload["parent_ref"] = foreign.genome_ref
    elif damage == "same_version":
        payload["manifest"]["agent_version"] = "0.2.0"
    elif damage == "older_version":
        payload["manifest"]["agent_version"] = "0.1.0"
    elif damage == "objective":
        payload["objective"]["minimum_improvement"] = "0"
    elif damage == "mission":
        payload["manifest"]["mission"] = "new authority"
    elif damage == "timeout":
        payload["manifest"]["timeout_seconds"] = parent.manifest.timeout_seconds + 1
    else:
        payload["sources"] = parent.model_dump(mode="json")["sources"]
    with pytest.raises(ValueError):
        registry.register(module().AgentGenome.model_validate(payload))
    assert len(registry.list()) == (2 if damage == "wrong_agent" else 1)


def test_edited_baseline_cannot_be_registered_as_a_new_trusted_root(tmp_path):
    payload = baseline().model_dump(mode="json")
    payload["sources"][0]["content"] = "# forged original"
    registry = store(tmp_path / "genomes.sqlite3")
    with pytest.raises(ValueError, match="baseline"):
        registry.register(module().AgentGenome.model_validate(payload))
    assert registry.list() == ()


def test_concurrent_idempotent_registration_has_one_audit_bound_record(tmp_path):
    path = tmp_path / "genomes.sqlite3"
    registry = store(path)
    root = registry.seed("A02", objective())
    candidate = child(root)
    stores = [store(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = tuple(pool.map(lambda entry: entry.register(candidate), stores))
    assert results == (candidate,) * 4
    assert len(registry.list()) == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2


@pytest.mark.parametrize("damage", ["content", "delete", "audit_binding", "head"])
def test_changed_archives_and_journal_refuse_reads_and_restart(tmp_path, damage):
    path = tmp_path / "genomes.sqlite3"
    registry = store(path)
    root = registry.seed("A02", objective())
    with sqlite3.connect(path) as db:
        if damage == "content":
            db.execute("UPDATE agent_genomes SET body=?", (b"{}",))
        elif damage == "delete":
            db.execute("DELETE FROM agent_genomes")
        elif damage == "audit_binding":
            from ai_quant_os.audit_chain import event_hash, GENESIS

            raw = json.dumps({"action": "wrong"}, sort_keys=True)
            db.execute("UPDATE audit_events SET event_json=?", (raw,))
            digest = event_hash(1, root.genome_ref, raw, GENESIS)
            db.execute("UPDATE audit_chain SET event_hash=?", (digest,))
            db.execute("UPDATE audit_head SET head_hash=?", (digest,))
        else:
            db.execute("UPDATE audit_head SET head_hash=?", ("0" * 64,))
    with pytest.raises(ValueError):
        registry.get(root.genome_ref)
    with pytest.raises(ValueError):
        store(path)


@pytest.mark.parametrize("field", ["execution_verified", "promotion_allowed"])
@pytest.mark.parametrize("value", [True, 0, "false", None])
def test_genome_flags_require_literal_false(field, value):
    payload = baseline().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValueError):
        module().AgentGenome.model_validate(payload)


def test_unknown_and_planned_agents_have_no_source_bootstrap():
    with pytest.raises(ValueError):
        baseline("A09")


def test_descendants_cannot_reexpand_previously_reduced_tool_capabilities(tmp_path):
    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())
    payload = child(root).model_dump(mode="json")
    payload["manifest"]["allowed_tools"] = []
    reduced = registry.register(module().AgentGenome.model_validate(payload))
    payload = child(reduced, version="0.4.0").model_dump(mode="json")
    payload["manifest"]["allowed_tools"] = list(root.manifest.allowed_tools)
    with pytest.raises(ValueError, match="policy"):
        registry.register(module().AgentGenome.model_validate(payload))
    assert len(registry.list()) == 2


def test_failed_audit_append_rolls_back_the_genome_record(tmp_path, monkeypatch):
    from ai_quant_os.audit_chain import AuditChain

    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("fixture audit unavailable")

    monkeypatch.setattr(AuditChain, "append", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        registry.register(child(root))
    assert registry.list() == (root,)


def test_saved_schema_accepts_real_archive_and_rejects_authority_claims():
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_path = Path(__file__).resolve().parents[1] / "data/schemas/agent-genome.schema.json"
    assert schema_path.exists(), "Missing portable genome schema"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    payload = baseline().model_dump(mode="json")
    validator.validate(payload)
    payload["promotion_allowed"] = True
    assert not validator.is_valid(payload)
