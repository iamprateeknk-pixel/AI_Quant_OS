from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("nautilus_trader")


def archived_workspace(tmp_path, failed=False):
    from pydantic import TypeAdapter
    from ai_quant_os.research_results import ResearchReport
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.strategy_registry import StrategyRegistry

    fixture = Path(__file__).parents[1] / "docs/evaluations/research-adversarial-baseline.json"
    report = TypeAdapter(ResearchReport).validate_python(json.loads(fixture.read_text())["report"])
    if failed:
        report = replace(
            report,
            outcome="FAILED",
            selection=None,
            folds=(),
            holdout=None,
            holdout_stress=None,
            gates=(),
            reasons=("INVALID_CANDIDATE",),
            attempts=(),
            monte_carlo=None,
            adversarial=None,
        )
    strategies = StrategyRegistry(tmp_path / "strategies")
    for attempt in report.attempts:
        strategies.register(attempt.binding.implementation)
    research = ResearchStore(tmp_path / "research.sqlite3")
    owned = research.reserve(report.plan)
    if report.selection is not None:
        research.freeze_selection(report.plan.experiment_id, owned.token, report.selection)
    research.finish(report.plan.experiment_id, owned.token, report)
    return report, research, strategies


def test_registry_retains_verified_dossier_and_original_source(tmp_path):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    bundle = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    view = store.register(bundle)
    assert view.state == "CHALLENGER"
    assert view.revision == 1
    assert view.manifest.promotion_allowed is False
    assert view.manifest.blocking_gates == ("INDEPENDENT_REVIEW", "SANDBOX", "SHADOW")
    assert bundle.report == report
    assert bundle.sources
    assert store.register(bundle) == view
    restarted = CandidateStore(store.path)
    assert restarted.list() == (view,)
    assert restarted.bundle(view.manifest.candidate_id) == bundle
    with pytest.raises(ValueError):
        replace(bundle.manifest, promotion_allowed=True)
    with pytest.raises(ValueError):
        replace(
            bundle, sources=(replace(bundle.sources[0], text="altered source"), *bundle.sources[1:])
        )
    with pytest.raises(ValueError):
        replace(bundle, manifest=replace(bundle.manifest, blocking_gates=()))


def test_failed_experiments_are_retained_without_a_selected_candidate(tmp_path):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path, failed=True)
    bundle = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    view = CandidateStore(tmp_path / "candidates.sqlite3").register(bundle)
    assert view.state == "FAILED"
    assert view.manifest.selected_genome is None
    assert view.manifest.reasons == ("INVALID_CANDIDATE",)
    assert bundle.sources == ()
    assert view.manifest.promotion_allowed is False


def test_unfinished_or_unknown_experiment_cannot_create_a_dossier(tmp_path):
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    with pytest.raises(ValueError, match="completed"):
        build_candidate_bundle(research, strategies, "unknown")
    new = replace(
        report.plan,
        experiment_id="uncertain",
        development_scope=replace(report.plan.development_scope, instrument_id="GBPUSD"),
        holdout_scope=replace(report.plan.holdout_scope, instrument_id="GBPUSD"),
    )
    research.reserve(new)
    with pytest.raises(ValueError, match="completed"):
        build_candidate_bundle(research, strategies, "uncertain")


def test_withdrawal_is_atomic_idempotent_and_revision_checked(tmp_path):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    view = store.register(build_candidate_bundle(research, strategies, report.plan.experiment_id))
    candidate_id = view.manifest.candidate_id

    def withdraw(command):
        try:
            return store.withdraw(
                candidate_id,
                expected_revision=1,
                command_id=command,
                reason="Retire synthetic evaluation",
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(2) as pool:
        outcomes = tuple(pool.map(withdraw, ("withdraw-a", "withdraw-b")))
    assert sum(not isinstance(row, str) for row in outcomes) == 1
    command = "withdraw-a" if not isinstance(outcomes[0], str) else "withdraw-b"
    final = store.get(candidate_id)
    assert final.state == "WITHDRAWN" and final.revision == 2
    assert (
        store.withdraw(
            candidate_id,
            expected_revision=1,
            command_id=command,
            reason="Retire synthetic evaluation",
        )
        == final
    )
    with pytest.raises(ValueError):
        store.withdraw(
            candidate_id, expected_revision=1, command_id=command, reason="Changed reason"
        )
    assert store.bundle(candidate_id).report == report


@pytest.mark.parametrize("tamper", ["bundle", "event", "identity", "shape"])
def test_registry_corruption_fails_closed(tmp_path, tamper):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    candidate_id = store.register(
        build_candidate_bundle(research, strategies, report.plan.experiment_id)
    ).manifest.candidate_id
    with sqlite3.connect(store.path) as db:
        if tamper == "bundle":
            db.execute("UPDATE research_candidates SET bundle=?", (b"{}",))
        elif tamper == "event":
            db.execute("DELETE FROM candidate_events")
        elif tamper == "shape":
            from hashlib import sha256

            db.execute(
                "UPDATE research_candidates SET bundle=?,bundle_sha256=?",
                (b"[]", sha256(b"[]").hexdigest()),
            )
        else:
            db.execute("UPDATE research_candidates SET candidate_id=?", ("0" * 64,))
    with pytest.raises(ValueError):
        store.get(candidate_id)


def test_candidate_api_requires_operator_and_uses_server_owned_archives(tmp_path):
    from fastapi.testclient import TestClient
    from ai_quant_os.api import create_app

    report, _, _ = archived_workspace(tmp_path)
    client = TestClient(create_app(operator_token="test-secret", research_workspace=tmp_path))
    url = "/api/v1/paper/research/candidates"
    payload = {"experiment_id": report.plan.experiment_id}
    assert client.post(url, json=payload).status_code == 401
    headers = {"x-operator-token": "test-secret"}
    assert (
        client.post(url, headers=headers, json={**payload, "promotion_allowed": True}).status_code
        == 422
    )
    created = client.post(url, headers=headers, json=payload)
    assert created.status_code == 200, created.text
    view = created.json()
    identifier = view["manifest"]["candidate_id"]
    assert view["state"] == "CHALLENGER"
    assert client.get("/api/v1/research/candidates").status_code == 401
    assert len(client.get("/api/v1/research/candidates", headers=headers).json()["candidates"]) == 1
    bundle = client.get(f"/api/v1/research/candidates/{identifier}/bundle", headers=headers)
    assert bundle.status_code == 200
    assert bundle.json()["manifest"]["promotion_allowed"] is False
    assert (
        client.get(
            "/api/v1/research/candidates/" + "0" * 64 + "/bundle", headers=headers
        ).status_code
        == 404
    )
    withdrawn = client.post(
        url + f"/{identifier}/withdraw",
        headers=headers,
        json={"expected_revision": 1, "command_id": "withdraw-api", "reason": "Retire fixture"},
    )
    assert withdrawn.status_code == 200 and withdrawn.json()["state"] == "WITHDRAWN"
    disabled = TestClient(create_app(operator_token="test-secret"))
    assert disabled.get("/api/v1/research/candidates", headers=headers).status_code == 503


def test_registration_rolls_back_if_journal_cannot_commit(tmp_path, monkeypatch):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    bundle = build_candidate_bundle(research, strategies, report.plan.experiment_id)

    def unavailable(*args):
        raise sqlite3.OperationalError("Journal unavailable")

    monkeypatch.setattr(store, "_append", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        store.register(bundle)
    assert CandidateStore(store.path).list() == ()


def test_concurrent_registration_keeps_one_entry_and_event(tmp_path):
    from ai_quant_os.research_candidate_store import CandidateStore
    from ai_quant_os.research_candidates import build_candidate_bundle

    report, research, strategies = archived_workspace(tmp_path)
    store = CandidateStore(tmp_path / "candidates.sqlite3")
    bundle = build_candidate_bundle(research, strategies, report.plan.experiment_id)
    with ThreadPoolExecutor(2) as pool:
        views = tuple(pool.map(store.register, (bundle, bundle)))
    assert views[0] == views[1]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0] == 1


def test_dossier_cannot_omit_native_evidence_source(tmp_path):
    from ai_quant_os.research_candidates import CandidateBundle, candidate_manifest
    from ai_quant_os.research_results import ResearchReport
    from pydantic import TypeAdapter

    record = json.loads(
        (Path(__file__).parents[1] / "docs/evaluations/research-native-baseline.json").read_text()
    )
    report = TypeAdapter(ResearchReport).validate_python(record["report"])
    stripped = replace(report, attempts=())
    with pytest.raises(ValueError, match="native evidence"):
        CandidateBundle("1.0", candidate_manifest(stripped), stripped, ())


def test_candidate_cli_registers_lists_and_exports_without_reexecuting(tmp_path):
    import subprocess
    import sys

    report, _, _ = archived_workspace(tmp_path)
    base = [sys.executable, "-m", "ai_quant_os.candidate_cli", "--workspace", str(tmp_path)]
    registered = subprocess.run(
        [*base, "register", report.plan.experiment_id], capture_output=True, text=True
    )
    assert registered.returncode == 0, registered.stderr
    identifier = json.loads(registered.stdout)["manifest"]["candidate_id"]
    listed = subprocess.run([*base, "list"], capture_output=True, text=True)
    assert listed.returncode == 0 and len(json.loads(listed.stdout)["candidates"]) == 1
    output = tmp_path / "bundle.json"
    exported = subprocess.run(
        [*base, "bundle", identifier, "--output", str(output)], capture_output=True, text=True
    )
    assert exported.returncode == 0, exported.stderr
    bundle = json.loads(output.read_text())
    assert bundle["manifest"]["promotion_allowed"] is False
    assert bundle["report"]["plan"]["experiment_id"] == report.plan.experiment_id
    before = output.read_bytes()
    repeated = subprocess.run(
        [*base, "bundle", identifier, "--output", str(output)], capture_output=True, text=True
    )
    assert repeated.returncode == 0 and output.read_bytes() == before
    protected = subprocess.run(
        [*base, "bundle", identifier, "--output", str(tmp_path / "research.sqlite3")],
        capture_output=True,
        text=True,
    )
    assert protected.returncode != 0
