"""Prospective evaluation artifacts only; no challenger/evaluator execution."""

import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_quant_os.agent_registry import load_agent_catalog
from test_agent_genomes import baseline


def models():
    assert importlib.util.find_spec("ai_quant_os.evolution_protocols"), "Missing retained protocols"
    return importlib.import_module("ai_quant_os.evolution_protocols")


def dataset(
    *, resource_class="TRAINING", name="regime-cases", case_id="case-1", input_json='{"price":"1"}'
):
    return models().EvolutionDataset(
        dataset_id=name,
        dataset_version="1.0.0",
        agent_id="A02",
        resource_class=resource_class,
        provenance="SYNTHETIC_DEVELOPMENT_NOT_HOLDOUT",
        cases=[
            {"case_id": case_id, "input_json": input_json, "expected_json": '{"regime":"TRENDING"}'}
        ],
    )


def protocol(data):
    return models().EvaluationProtocol(
        protocol_id="regime-correctness",
        protocol_version="1.0.0",
        agent_id="A02",
        output_type="RegimeDistribution",
        dataset_ref=data.dataset_ref,
        primary_metric="CORRECTNESS",
        criteria=[
            {
                "criterion": {
                    "metric": "CORRECTNESS",
                    "direction": "MAXIMIZE",
                    "minimum_improvement": "0.01",
                },
                "method": "EXACT_OBJECT_MATCH",
                "maximum_regression": "0",
            }
        ],
    )


def store(path):
    assert importlib.util.find_spec("ai_quant_os.evolution_catalog"), (
        "Missing evaluation artifact catalog"
    )
    return importlib.import_module("ai_quant_os.evolution_catalog").EvolutionCatalog(path)


def test_protocol_retains_dataset_and_yields_an_exact_objective_pin(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    data = catalog.register(dataset())
    plan = catalog.register(protocol(data))
    reopened = store(tmp_path / "catalog.sqlite3")
    assert reopened.protocol(plan.protocol_sha256) == plan
    assert plan.objective.protocol_sha256 == plan.protocol_sha256
    assert plan.objective.metric == "CORRECTNESS"
    assert plan.promotion_allowed is False
    assert plan.execution_verified is False
    records = reopened.inventory()
    assert len(records) == 2
    assert all("cases" not in row and "input_json" not in row for row in records)


def test_generator_cannot_read_holdout_and_validator_can_read_retained_bytes(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    data = catalog.register(dataset(resource_class="HOLDOUT"))
    registry = load_agent_catalog()
    with pytest.raises(PermissionError):
        catalog.dataset(data.dataset_ref, registry=registry, agent_id="A09", agent_version="0.1.0")
    assert (
        catalog.dataset(data.dataset_ref, registry=registry, agent_id="A10", agent_version="0.1.0")
        == data
    )


def test_training_reads_require_current_role_and_quarantine_admission(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    data = catalog.register(dataset())
    registry = load_agent_catalog()
    assert (
        catalog.dataset(data.dataset_ref, registry=registry, agent_id="A09", agent_version="0.1.0")
        == data
    )
    manifest = registry.get("A09", "0.1.0")
    registry.quarantine("A09", "0.1.0", manifest.manifest_ref)
    with pytest.raises(PermissionError):
        catalog.dataset(data.dataset_ref, registry=registry, agent_id="A09", agent_version="0.1.0")


def test_renamed_inputs_cannot_cross_training_holdout_classification(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    catalog.register(dataset())
    with pytest.raises(ValueError, match="overlap"):
        catalog.register(
            dataset(resource_class="HOLDOUT", name="renamed", case_id="different-name")
        )
    assert len(catalog.inventory()) == 1


def test_same_dataset_version_is_immutable_and_same_content_is_idempotent(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    data = dataset()
    assert catalog.register(data) == catalog.register(data)
    with pytest.raises(ValueError, match="version"):
        catalog.register(dataset(input_json='{"price":"2"}'))


def test_protocol_cannot_predeclare_missing_or_foreign_dataset(tmp_path):
    catalog = store(tmp_path / "catalog.sqlite3")
    plan = protocol(dataset())
    with pytest.raises(ValueError):
        catalog.register(plan)
    foreign = dataset().model_dump(mode="json")
    foreign["agent_id"] = "A07"
    data = catalog.register(models().EvolutionDataset.model_validate(foreign))
    wrong = plan.model_dump(mode="json")
    wrong["dataset_ref"] = data.dataset_ref
    with pytest.raises(ValueError):
        catalog.register(models().EvaluationProtocol.model_validate(wrong))


@pytest.mark.parametrize(
    "damage", ["duplicate_case", "noncanonical", "duplicate_input", "nan", "authority"]
)
def test_dataset_rejects_ambiguous_input_and_authority_claims(damage):
    payload = dataset().model_dump(mode="json")
    if damage == "duplicate_case":
        payload["cases"] *= 2
    elif damage == "duplicate_input":
        payload["cases"].append({**payload["cases"][0], "case_id": "case-2"})
    elif damage == "noncanonical":
        payload["cases"][0]["input_json"] = '{ "price": "1" }'
    elif damage == "nan":
        payload["cases"][0]["input_json"] = '{"value":NaN}'
    else:
        payload["promotion_allowed"] = True
    with pytest.raises(ValueError):
        models().EvolutionDataset.model_validate(payload)


@pytest.mark.parametrize(
    "damage",
    ["method", "direction", "duplicate_metric", "missing_primary", "output_type", "authority"],
)
def test_protocol_rejects_inconsistent_measurement_definitions(damage):
    payload = protocol(dataset()).model_dump(mode="json")
    if damage == "method":
        payload["criteria"][0]["method"] = "P95_WALL_MS"
    elif damage == "direction":
        payload["criteria"][0]["criterion"]["direction"] = "MINIMIZE"
    elif damage == "duplicate_metric":
        payload["criteria"] *= 2
    elif damage == "missing_primary":
        payload["primary_metric"] = "LATENCY_MS"
    elif damage == "output_type":
        payload["output_type"] = "OrderCommand"
    else:
        payload["execution_verified"] = True
    with pytest.raises(ValueError):
        models().EvaluationProtocol.model_validate(payload)


def test_catalog_refuses_changed_record_and_valid_chain_wrong_binding(tmp_path):
    from ai_quant_os.audit_chain import event_hash, GENESIS

    path = tmp_path / "catalog.sqlite3"
    catalog = store(path)
    data = catalog.register(dataset())
    with sqlite3.connect(path) as db:
        raw = json.dumps({"action": "wrong"}, sort_keys=True)
        digest = event_hash(1, data.dataset_ref, raw, GENESIS)
        db.execute("UPDATE audit_events SET event_json=?", (raw,))
        db.execute("UPDATE audit_chain SET event_hash=?", (digest,))
        db.execute("UPDATE audit_head SET head_hash=?", (digest,))
    with pytest.raises(ValueError):
        catalog.inventory()
    with pytest.raises(ValueError):
        store(path)


def test_opaque_legacy_genome_protocol_pin_cannot_match_a_retained_plan():
    with pytest.raises(ValueError):
        models().require_protocol_genome(protocol(dataset()), baseline())


def test_matching_genome_retains_full_protocol_objective():
    from ai_quant_os.agent_genomes import capture_baseline_genome

    plan = protocol(dataset())
    genome = capture_baseline_genome(load_agent_catalog(), "A02", plan.objective)
    models().require_protocol_genome(plan, genome)


def test_concurrent_registration_commits_one_record_and_event(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    catalogs = [store(path) for _ in range(4)]
    data = dataset()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = tuple(pool.map(lambda catalog: catalog.register(data), catalogs))
    assert results == (data,) * 4
    assert len(store(path).inventory()) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1


def test_audit_append_failure_rolls_back_body(tmp_path, monkeypatch):
    from ai_quant_os.audit_chain import AuditChain

    catalog = store(tmp_path / "catalog.sqlite3")

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("fixture audit failure")

    monkeypatch.setattr(AuditChain, "append", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        catalog.register(dataset())
    assert catalog.inventory() == ()


@pytest.mark.parametrize("damage", ["body", "delete", "head"])
def test_storage_damage_refuses_read_and_restart(tmp_path, damage):
    path = tmp_path / "catalog.sqlite3"
    catalog = store(path)
    catalog.register(dataset())
    with sqlite3.connect(path) as db:
        if damage == "body":
            db.execute("UPDATE evolution_artifacts SET body=?", (b"{}",))
        elif damage == "delete":
            db.execute("DELETE FROM evolution_artifacts")
        else:
            db.execute("UPDATE audit_head SET head_hash=?", ("0" * 64,))
    with pytest.raises(ValueError):
        catalog.inventory()
    with pytest.raises(ValueError):
        store(path)


@pytest.mark.parametrize("kind", ["dataset", "protocol"])
def test_portable_schemas_accept_artifacts_and_refuse_authority(kind):
    from jsonschema import Draft202012Validator

    path = Path(__file__).resolve().parents[1] / f"data/schemas/evolution-{kind}.schema.json"
    assert path.exists(), "Missing portable evaluation schema"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    payload = (dataset() if kind == "dataset" else protocol(dataset())).model_dump(mode="json")
    validator.validate(payload)
    payload["execution_verified"] = True
    assert not validator.is_valid(payload)


def test_excessively_nested_case_input_refuses_as_validation_error():
    payload = dataset().model_dump(mode="json")
    payload["cases"][0]["input_json"] = '{"nested":' + "[" * 1100 + "0" + "]" * 1100 + "}"
    with pytest.raises(ValueError):
        models().EvolutionDataset.model_validate(payload)
