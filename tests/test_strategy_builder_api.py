import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app
from test_research_hypothesis_api import NOW, HEADERS, seed, run


def review(client):
    seed(client)
    _, _, output = run(client)
    from ai_quant_os.research_hypothesis_contracts import ResearchHypothesis

    signature = ResearchHypothesis.model_validate(output["hypotheses"][0]).signature
    response = client.post(
        "/api/v1/sandbox/research/reviews",
        headers=HEADERS,
        json={
            "review_id": "review-1",
            "run_id": "research-1",
            "hypothesis_signature": signature,
            "rationale": "Build an inert candidate for independent validation.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["review"]


def build(client, reviewed, task="build-1"):
    response = client.post(
        "/api/v1/sandbox/strategy-builder/context",
        headers=HEADERS,
        json={"task_id": task, "review_ref": reviewed["review_ref"]},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    response = client.post(
        "/api/v1/sandbox/strategy-builder/execute",
        headers=HEADERS,
        json={"run_id": task, "context_ref": context["context_ref"]},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "COMPLETED", receipt
    output = json.loads(receipt["output_json"])
    response = client.get(
        "/api/v1/sandbox/strategy-builder/candidates/" + output["candidate_ref"], headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return context, receipt, output, response.json()["candidate"]


def test_reviewed_hypothesis_builds_retained_inert_candidate_and_restarts_exactly(tmp_path):
    settings = dict(
        operator_token="fixture-hypothesis",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**settings)) as client:
        reviewed = review(client)
        before = client.get("/api/v1/system/status").json()
        context, receipt, output, candidate = build(client, reviewed)
        assert output["agent_id"] == "A09" and output["agent_version"] == "0.2.0"
        assert not output["execution_authorized"] and not output["promotion_authorized"]
        assert not output["isolation_verified"] and output["tests_status"] == "NOT_RUN"
        assert candidate["review"] == {
            key: value for key, value in reviewed.items() if key != "review_ref"
        }
        assert output["review_ref"] == reviewed["review_ref"]
        assert candidate["genome"]["input_kind"] == "BAR"
        assert [row["path"] for row in candidate["files"]] == ["candidate.py", "test_candidate.py"]
        assert client.get("/api/v1/system/status").json() == before
        assert (
            client.post(
                "/api/v1/paper/agents/execute",
                headers=HEADERS,
                json={"run_id": "wrong-scope", "context_ref": context["context_ref"]},
            ).status_code
            == 403
        )
    with TestClient(create_app(**settings)) as client:
        assert (
            client.get("/api/v1/agents/graph-runs/build-1", headers=HEADERS).json()["receipt"]
            == receipt
        )
        saved = client.get(
            "/api/v1/sandbox/strategy-builder/candidates/" + output["candidate_ref"],
            headers=HEADERS,
        )
        assert saved.json()["candidate"] == candidate


def test_withdrawn_review_blocks_candidate_context_and_preserves_original_review(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        response = client.post(
            "/api/v1/sandbox/research/reviews/" + reviewed["review_ref"] + "/withdraw",
            headers=HEADERS,
            json={"reason": "Need independent evidence first."},
        )
        assert response.status_code == 200, response.text
        denied = client.post(
            "/api/v1/sandbox/strategy-builder/context",
            headers=HEADERS,
            json={"task_id": "denied", "review_ref": reviewed["review_ref"]},
        )
        assert denied.status_code == 403
        saved = client.get(
            "/api/v1/sandbox/research/reviews/" + reviewed["review_ref"], headers=HEADERS
        )
        assert saved.json()["review"] == reviewed and not saved.json()["build_authorized_current"]


def test_review_admission_checks_enabled_health_monitor(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=True,
        )
    ) as client:
        reviewed = review(client)
        assert reviewed["decision"] == "AUTHORIZE_INERT_CANDIDATE_BUILD"


def test_withdrawal_after_context_capture_prevents_candidate_publication(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        compiled = client.post(
            "/api/v1/sandbox/strategy-builder/context",
            headers=HEADERS,
            json={"task_id": "withdrawn", "review_ref": reviewed["review_ref"]},
        ).json()
        client.post(
            "/api/v1/sandbox/research/reviews/" + reviewed["review_ref"] + "/withdraw",
            headers=HEADERS,
            json={"reason": "Withdraw before construction."},
        ).raise_for_status()
        response = client.post(
            "/api/v1/sandbox/strategy-builder/execute",
            headers=HEADERS,
            json={"run_id": "withdrawn", "context_ref": compiled["context_ref"]},
        )
        assert response.status_code == 200 and response.json()["receipt"]["status"] == "DENIED"
        assert response.json()["receipt"]["output_json"] is None


def test_review_retry_never_extends_original_expiry(tmp_path):
    from datetime import timedelta
    from ai_quant_os.research_hypothesis_contracts import ResearchHypothesis

    clock = [NOW]
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: clock[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        clock[0] += timedelta(hours=1)
        payload = {
            "review_id": "review-1",
            "run_id": "research-1",
            "hypothesis_signature": ResearchHypothesis.model_validate(
                reviewed["hypothesis"]
            ).signature,
            "rationale": reviewed["rationale"],
        }
        response = client.post("/api/v1/sandbox/research/reviews", headers=HEADERS, json=payload)
        assert response.status_code == 200 and response.json()["review"] == reviewed
        assert (
            client.post(
                "/api/v1/sandbox/strategy-builder/context",
                headers=HEADERS,
                json={"task_id": "expired-review", "review_ref": reviewed["review_ref"]},
            ).status_code
            == 403
        )
        payload["rationale"] = "Changed rationale."
        assert (
            client.post(
                "/api/v1/sandbox/research/reviews", headers=HEADERS, json=payload
            ).status_code
            == 409
        )


def test_preview_and_missing_identity_cannot_review_or_build(tmp_path):
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/sandbox/research/reviews", json={}).status_code == 503
        assert client.post("/api/v1/sandbox/strategy-builder/context", json={}).status_code == 503
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        assert client.post("/api/v1/sandbox/research/reviews", json={}).status_code == 401
        assert (
            client.post(
                "/api/v1/sandbox/strategy-builder/context",
                headers=HEADERS,
                json={
                    "task_id": "bad-scope",
                    "review_ref": "hypothesis-review:" + "0" * 64,
                    "scope": "PAPER",
                },
            ).status_code
            == 422
        )


def test_post_publication_expiry_retains_only_inert_archive_without_success_output(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    from ai_quant_os.strategy_build_store import StrategyBuildStore

    clock = [NOW]
    original = StrategyBuildStore.put_candidate
    stored = []

    def delayed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        stored.append(result)
        clock[0] += timedelta(seconds=5)
        return result

    monkeypatch.setattr(StrategyBuildStore, "put_candidate", delayed)
    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: clock[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        context = client.post(
            "/api/v1/sandbox/strategy-builder/context",
            headers=HEADERS,
            json={"task_id": "expiry", "review_ref": reviewed["review_ref"]},
        ).json()
        response = client.post(
            "/api/v1/sandbox/strategy-builder/execute",
            headers=HEADERS,
            json={"run_id": "expiry", "context_ref": context["context_ref"]},
        )
        assert response.json()["receipt"]["status"] == "EXPIRED"
        assert response.json()["receipt"]["output_json"] is None and len(stored) == 1
        archival = client.get(
            "/api/v1/sandbox/strategy-builder/candidates/" + stored[0].candidate_ref,
            headers=HEADERS,
        )
        assert (
            archival.status_code == 200
            and archival.json()["candidate"]["tests_status"] == "NOT_RUN"
        )


def test_review_status_does_not_claim_current_build_authority_for_limited_agent(
    tmp_path, monkeypatch
):
    from ai_quant_os.agent_registry import AgentRegistry

    original = AgentRegistry.require_tool

    def require(self, agent, version, tool, *, scope):
        if agent == "A09" and tool == "CANDIDATE_CREATE":
            raise PermissionError("Controlled current role restriction")
        return original(self, agent, version, tool, scope=scope)

    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        monkeypatch.setattr(AgentRegistry, "require_tool", require)
        response = client.get(
            "/api/v1/sandbox/research/reviews/" + reviewed["review_ref"], headers=HEADERS
        )
        assert response.status_code == 200 and not response.json()["build_authorized_current"]


def test_context_admission_rechecks_expiry_after_final_owned_review_read(tmp_path, monkeypatch):
    from datetime import timedelta
    from ai_quant_os.strategy_build_store import StrategyBuildStore

    clock = [NOW]
    original = StrategyBuildStore.active
    count = [0]

    def delayed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        count[0] += 1
        if count[0] == 2:
            clock[0] += timedelta(seconds=5)
        return result

    with TestClient(
        create_app(
            operator_token="fixture-hypothesis",
            clock=lambda: clock[0],
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        reviewed = review(client)
        monkeypatch.setattr(StrategyBuildStore, "active", delayed)
        response = client.post(
            "/api/v1/sandbox/strategy-builder/context",
            headers=HEADERS,
            json={"task_id": "late-review-read", "review_ref": reviewed["review_ref"]},
        )
        assert response.status_code == 503
