from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_research_evidence_catalog import NOW, raw_bars

HEADERS = {"x-operator-token": "fixture-research-data"}


def publication(resource_class="TRAINING", minute=0):
    return {
        "source_id": "declared-history",
        "resource_class": resource_class,
        "csv": raw_bars(minute=minute).decode(),
    }


def test_operator_publication_retains_checked_metadata_without_changing_paper_controls(tmp_path):
    settings = dict(
        operator_token="fixture-research-data",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**settings)) as client:
        before = client.get("/api/v1/system/status").json()
        response = client.post(
            "/api/v1/paper/research/datasets", headers=HEADERS, json=publication()
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
        assert receipt["resource_class"] == "TRAINING" and receipt["row_count"] == 3
        assert not receipt["execution_authorized"]
        assert receipt["market_authenticity"] == "UNVERIFIED"
        overlap = client.post(
            "/api/v1/paper/research/datasets", headers=HEADERS, json=publication("HOLDOUT")
        )
        assert overlap.status_code == 409
        heldout = client.post(
            "/api/v1/paper/research/datasets",
            headers=HEADERS,
            json=publication("HOLDOUT", minute=10),
        )
        assert heldout.status_code == 200
        records = client.get("/api/v1/research/datasets", headers=HEADERS).json()
        assert len(records["datasets"]) == 2
        assert all("csv" not in row for row in records["datasets"])
        assert client.get("/api/v1/system/status").json() == before
    with TestClient(create_app(**settings)) as client:
        assert client.get("/api/v1/research/datasets", headers=HEADERS).json() == records
        retry = client.post("/api/v1/paper/research/datasets", headers=HEADERS, json=publication())
        assert retry.json()["receipt"] == receipt


def test_research_data_routes_require_operator_access_and_refuse_extra_authority(tmp_path):
    with TestClient(
        create_app(operator_token="fixture-research-data", paper_db_path=tmp_path / "paper.sqlite3")
    ) as client:
        assert client.get("/api/v1/research/datasets").status_code == 401
        assert client.post("/api/v1/paper/research/datasets", json=publication()).status_code == 401
        assert (
            client.get(
                "/api/v1/research/datasets", headers={"x-operator-token": "wrong"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/paper/research/datasets",
                headers=HEADERS,
                json={**publication(), "execution_authorized": True},
            ).status_code
            == 422
        )
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/research/datasets").status_code == 503
        assert client.post("/api/v1/paper/research/datasets", json=publication()).status_code == 503


def test_changed_raw_data_blocks_operator_inspection_without_healing(tmp_path):
    path = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(operator_token="fixture-research-data", clock=lambda: NOW, paper_db_path=path)
    ) as client:
        response = client.post(
            "/api/v1/paper/research/datasets", headers=HEADERS, json=publication()
        )
        assert response.status_code == 200
        with closing(sqlite3.connect(path.with_name("paper.research-data.sqlite3"))) as db, db:
            db.execute("UPDATE research_datasets SET raw_csv=?", (b"changed",))
        assert client.get("/api/v1/research/datasets", headers=HEADERS).status_code == 503
