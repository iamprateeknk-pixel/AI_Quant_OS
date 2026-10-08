import json
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from ai_quant_os.api import create_app
from test_research_candidates import archived_workspace


def registry(tmp_path):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    bundle = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    store.register(bundle)
    return store, bundle


def test_research_page_retains_checked_gates_without_source_or_promotion(tmp_path):
    store, bundle = registry(tmp_path)
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        url = "/api/v1/research/candidates/page"
        assert client.get(url).status_code == 401
        headers = {"X-Operator-Token": "operator"}
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 1
        row = page["candidates"][0]
        assert row["candidate_id"] == bundle.manifest.candidate_id
        assert row["state"] == "CHALLENGER" and row["promotion_allowed"] is False
        assert row["gates"] == [
            {"name": g.name, "state": g.state, "reason": g.reason} for g in bundle.report.gates
        ]
        assert row["report_sha256"] == bundle.manifest.report_sha256
        assert "text" not in json.dumps(page) and "sources" not in row
        store.withdraw(
            row["candidate_id"],
            expected_revision=1,
            command_id="withdraw-fixture",
            reason="fixture",
        )
        assert (
            client.get(url, params={"head": page["head_hash"]}, headers=headers).status_code == 409
        )
        assert client.get(url, params={"offset": 1}, headers=headers).status_code == 422
        assert client.get(url, headers=headers).json()["candidates"][0]["state"] == "WITHDRAWN"


def test_rehashed_corrupt_report_cannot_be_displayed_as_verified(tmp_path):
    store, _ = registry(tmp_path)
    from hashlib import sha256

    with sqlite3.connect(store.path) as db:
        raw = db.execute("SELECT bundle FROM research_candidates").fetchone()[0]
        record = json.loads(raw)
        record["report"]["reasons"] = ["forged"]
        changed = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        db.execute(
            "UPDATE research_candidates SET bundle=?,bundle_sha256=?",
            (changed, sha256(changed).hexdigest()),
        )
    with TestClient(create_app(operator_token="operator", research_workspace=tmp_path)) as client:
        response = client.get(
            "/api/v1/research/candidates/page", headers={"X-Operator-Token": "operator"}
        )
        assert response.status_code == 503
        assert "forged" not in response.text


def test_dossier_schema_matches_runtime_and_previews_fit_the_transport():
    from types import SimpleNamespace
    from ai_quant_os.research_dossier_views import ResearchDossierPage, dossier_preview

    manifest = SimpleNamespace(
        candidate_id="a" * 64,
        experiment_id="e" * 64,
        hypothesis_id="h" * 64,
        research_outcome="FAILED",
        report_sha256="b" * 64,
        plan_sha256="c" * 64,
        selected_genome=None,
    )
    view = SimpleNamespace(
        manifest=manifest, state="FAILED", revision=1, last_event_sha256="d" * 64
    )
    report = SimpleNamespace(
        plan=SimpleNamespace(hypothesis=SimpleNamespace(description="\x00" * 4096)),
        gates=[SimpleNamespace(name="\x00" * 128, state="FAIL", reason="\x00" * 4096)] * 32,
        reasons=["\x00" * 4096] * 32,
        folds=(),
        attempts=(),
    )
    row = dossier_preview(view, SimpleNamespace(report=report))
    assert row.details_truncated is True
    page = ResearchDossierPage(
        head_hash="f" * 64, total=2, offset=0, limit=2, next_offset=None, candidates=(row, row)
    )
    assert len(page.model_dump_json().encode()) < 131072
    assert (
        json.loads(Path("docs/schemas/research-dossier-page.schema.json").read_text())
        == ResearchDossierPage.model_json_schema()
    )
