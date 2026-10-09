import json
import sqlite3
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_agent_genomes import child, objective, store


URL = "/api/v1/evolution/genomes/page"
HEADERS = {"X-Operator-Token": "operator"}


def test_checked_genome_page_is_private_bounded_and_head_bound(tmp_path, monkeypatch):
    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())
    candidate = registry.register(child(root))
    monkeypatch.setenv("AI_QUANT_EVOLUTION_WORKSPACE", str(tmp_path))
    with TestClient(create_app(operator_token="operator")) as client:
        assert client.get(URL).status_code == 401
        response = client.get(URL, params={"limit": 1}, headers=HEADERS)
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 2 and page["next_offset"] == 1
        continued = client.get(
            URL, params={"limit": 1, "offset": 1, "head": page["head_hash"]}, headers=HEADERS
        ).json()
        rows = page["genomes"] + continued["genomes"]
        challenger = next(row for row in rows if row["genome_ref"] == candidate.genome_ref)
        assert challenger["parent_ref"] == root.genome_ref
        assert challenger["state"] == "UNVERIFIED_CHALLENGER"
        assert challenger["execution_verified"] is False
        assert challenger["promotion_allowed"] is False
        assert challenger["source_inventory"] == [
            {"path": s.path, "sha256": s.sha256} for s in candidate.sources
        ]
        assert "challenger must never run" not in json.dumps(rows)
        assert "content" not in json.dumps(rows) and "prompt_template" not in json.dumps(rows)
        assert client.get(URL, params={"offset": 1}, headers=HEADERS).status_code == 422
        assert client.get(URL, params={"limit": 11}, headers=HEADERS).status_code == 422
        registry.register(child(candidate, version="0.4.0"))
        assert (
            client.get(URL, params={"head": page["head_hash"]}, headers=HEADERS).status_code == 409
        )


def test_offpage_corruption_withholds_all_genomes(tmp_path, monkeypatch):
    registry = store(tmp_path / "genomes.sqlite3")
    root = registry.seed("A02", objective())
    registry.register(child(root))
    monkeypatch.setenv("AI_QUANT_EVOLUTION_WORKSPACE", str(tmp_path))
    with sqlite3.connect(registry.path) as db:
        ref = db.execute(
            "SELECT genome_ref FROM agent_genomes ORDER BY genome_ref DESC LIMIT 1"
        ).fetchone()[0]
        db.execute("UPDATE agent_genomes SET body=? WHERE genome_ref=?", (b'{"forged":true}', ref))
    with TestClient(create_app(operator_token="operator")) as client:
        response = client.get(URL, params={"limit": 1}, headers=HEADERS)
        assert response.status_code == 503 and "forged" not in response.text


def test_missing_archive_is_unavailable_without_creating_one(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_QUANT_EVOLUTION_WORKSPACE", str(tmp_path / "missing"))
    with TestClient(create_app(operator_token="operator")) as client:
        assert client.get(URL, headers=HEADERS).status_code == 503
    assert not (tmp_path / "missing").exists()


def test_genome_page_schema_and_response_bound():
    from ai_quant_os.evolution_views import EvolutionGenomePage, genome_preview
    from test_agent_genomes import baseline

    row = genome_preview(baseline()).model_dump(mode="json")
    row["source_inventory"] = [{"path": "\x00" * 128, "sha256": "a" * 64}]
    rows = [{**row, "genome_ref": "agent-genome:" + f"{number:064x}"} for number in range(10)]
    payload = {
        "head_hash": "a" * 64,
        "total": 10,
        "offset": 0,
        "limit": 10,
        "next_offset": None,
        "genomes": rows,
    }
    page = EvolutionGenomePage.model_validate(payload)
    assert len(page.model_dump_json().encode()) < 131072
    for bad in [
        {**payload, "total": 0},
        {**payload, "next_offset": 10},
        {**payload, "genomes": [rows[0]] * 10},
    ]:
        with pytest.raises(ValueError):
            EvolutionGenomePage.model_validate(bad)
    assert (
        json.loads(Path("docs/schemas/evolution-genome-page.schema.json").read_text())
        == EvolutionGenomePage.model_json_schema()
    )


def test_long_decimal_objective_is_not_rounded_or_exposed_without_bound():
    from ai_quant_os.evolution_views import genome_preview
    from test_agent_genomes import baseline
    from ai_quant_os.agent_genomes import EvaluationObjective

    genome = baseline().model_copy(
        update={
            "objective": EvaluationObjective(
                metric="CORRECTNESS",
                direction="MAXIMIZE",
                minimum_improvement="0." + "1" * 150000,
                protocol_sha256="a" * 64,
            )
        }
    )
    preview = genome_preview(genome)
    assert len(preview.model_dump_json().encode()) < 10000
    assert preview.objective.minimum_improvement is None
    assert preview.details_truncated is True
