"""Durable quota admission at the actual context source-fetch boundary."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Event

import pytest

from fastapi.testclient import TestClient

from ai_quant_os.agent_call_budgets import AgentCallBudget, AgentCallPolicy, AgentCallRule
from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
from ai_quant_os.api import create_app
from test_strategy_prefix_worker import package as package

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def policy(calls=2, reserve=1):
    return AgentCallPolicy(
        tuple(AgentCallRule(f"A{i:02}", calls, 10, reserve, reserve) for i in range(1, 19))
    )


def evidence():
    return EvidenceRecord(
        source_id="fixed-trusted-test",
        as_of=NOW,
        valid_until=NOW + timedelta(seconds=3),
        trusted=True,
        payload_json='{"observation":1}',
    )


def exhausted_role_policy(agent_id):
    return AgentCallPolicy(
        tuple(
            AgentCallRule(f"A{i:02}", 0 if f"A{i:02}" == agent_id else 100, 1000, 0, 0)
            for i in range(1, 19)
        )
    )


def test_context_fetch_and_graph_dispatch_share_one_original_role_quota(tmp_path):
    path = tmp_path / "calls.sqlite3"
    guard = AgentCallBudget(path, policy=policy(), clock=lambda: NOW)
    calls = []

    async def fetch():
        assert guard.status()["agents"][1]["daily_calls"] == 1
        calls.append("source")
        return evidence()

    async def handler(invocation):
        calls.append("tool")
        return {}

    async def run():
        registry = load_agent_catalog()
        compiler = ContextCompiler(
            registry,
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
            timeout_seconds=10,
            overall_timeout_seconds=30,
            call_budget=guard,
        )
        result = await compiler.compile("A02", "0.1.0", task_id="context", scope="PAPER")
        assert result.inputs[0].status == "AVAILABLE"
        reopened = AgentCallBudget(path, policy=None, clock=lambda: NOW)
        boundary = AgentToolBoundary(registry, {"WORLD_STATE_READ": handler}, call_budget=reopened)
        try:
            await boundary.call("A02", "0.2.0", "WORLD_STATE_READ", scope="PAPER", payload={})
        except PermissionError:
            pass
        else:
            raise AssertionError("Source fetch must consume shared role capacity")
        denied = await compiler.compile("A02", "0.2.0", task_id="again", scope="PAPER")
        assert denied.inputs[0].status == "DENIED"
        assert denied.input_refs == ()

    asyncio.run(run())
    assert calls == ["source"]
    assert guard.status()["agents"][1]["daily_recovery_reserve"] == 1


def test_actual_context_api_observes_quota_and_emergency_controls_remain_available(tmp_path):
    headers = {"X-Operator-Token": "operator"}
    path = tmp_path / "paper.sqlite3"
    with TestClient(
        create_app(
            paper_db_path=path,
            operator_token="operator",
            clock=lambda: NOW,
            agent_call_policy=policy(0, 0),
        )
    ) as client:
        result = client.post(
            "/api/v1/paper/agents/context",
            headers=headers,
            json={"agent_id": "A02", "agent_version": "0.1.0", "task_id": "fixed-context"},
        )
        assert result.status_code == 200
        assert result.json()["input_refs"] == []
        assert all(row["status"] in {"MISSING", "DENIED"} for row in result.json()["inputs"])
        status = client.get("/api/v1/agents/call-budget", headers=headers).json()
        assert status["agents"][1]["daily_calls"] == 0
        assert (
            client.post(
                "/api/v1/control/no-new-risk",
                headers={**headers, "Idempotency-Key": "context-budget-safety"},
                json={"reason": "fixed quota safety test"},
            ).status_code
            == 200
        )


def test_denied_holdout_and_missing_sources_do_not_consume_capacity(tmp_path):
    guard = AgentCallBudget(tmp_path / "calls.sqlite3", policy=policy(), clock=lambda: NOW)
    calls = []

    async def fetch():
        calls.append(1)
        return evidence()

    registry = load_agent_catalog()
    name = registry.get("A09", "0.1.0").required_inputs[0]
    result = asyncio.run(
        ContextCompiler(
            registry,
            {name: SourceBinding("WORLD_STATE_READ", "HOLDOUT", fetch)},
            clock=lambda: NOW,
            call_budget=guard,
        ).compile("A09", "0.1.0", task_id="denied-holdout", scope="SANDBOX")
    )
    assert result.inputs[0].status == "DENIED"
    assert calls == []
    assert all(row["daily_calls"] == 0 for row in guard.status()["agents"])


@pytest.mark.parametrize("kind", ["FAILED", "TIMEOUT", "UNTRUSTED"])
def test_source_failures_and_invalid_evidence_retain_reservation(tmp_path, kind):
    guard = AgentCallBudget(tmp_path / "calls.sqlite3", policy=policy(), clock=lambda: NOW)

    async def fetch():
        if kind == "FAILED":
            raise RuntimeError("private-evidence-detail")
        if kind == "TIMEOUT":
            await asyncio.sleep(10)
        return evidence().model_copy(update={"trusted": False})

    result = asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
            call_budget=guard,
            timeout_seconds=0.01,
        ).compile("A02", "0.1.0", task_id="source-failure", scope="PAPER")
    )
    assert result.inputs[0].status == kind
    assert result.input_refs == ()
    assert "private-evidence-detail" not in result.model_dump_json()
    assert guard.status()["agents"][1]["daily_calls"] == 1


def test_concurrent_context_sources_cannot_spend_recovery_capacity(tmp_path):
    path = tmp_path / "calls.sqlite3"
    guard = AgentCallBudget(path, policy=policy(), clock=lambda: NOW)
    calls = []

    async def fetch():
        calls.append(1)
        return evidence()

    registry = load_agent_catalog()
    result = asyncio.run(
        ContextCompiler(
            registry,
            {
                name: SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
                for name in registry.get("A02", "0.1.0").required_inputs
            },
            clock=lambda: NOW,
            call_budget=guard,
        ).compile("A02", "0.1.0", task_id="concurrent", scope="PAPER")
    )
    assert len(calls) == 1
    assert sum(row.status == "AVAILABLE" for row in result.inputs) == 1
    assert all(row.status in {"AVAILABLE", "DENIED"} for row in result.inputs)
    restored = AgentCallBudget(path, policy=None, clock=lambda: NOW)
    assert restored.status()["agents"][1]["daily_calls"] == 1
    assert restored.status()["agents"][1]["daily_remaining"] == 0


def test_permission_revoked_during_reservation_prevents_source_fetch(tmp_path):
    registry = load_agent_catalog()
    guard = AgentCallBudget(tmp_path / "calls.sqlite3", policy=policy(), clock=lambda: NOW)
    calls = []

    class RevokingBudget:
        def reserve(self, invocation):
            guard.reserve(invocation)
            registry.set_lifecycle("A02", "0.1.0", "QUARANTINED")

    async def fetch():
        calls.append(1)
        return evidence()

    result = asyncio.run(
        ContextCompiler(
            registry,
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
            call_budget=RevokingBudget(),
        ).compile("A02", "0.1.0", task_id="revoked", scope="PAPER")
    )
    assert result.inputs[0].status == "DENIED"
    assert calls == []
    assert guard.status()["agents"][1]["daily_calls"] == 1


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_or_overall_timeout_during_admission_never_fetches(tmp_path, cancel):
    guard = AgentCallBudget(tmp_path / "calls.sqlite3", policy=policy(), clock=lambda: NOW)
    started, release, finished = Event(), Event(), Event()
    calls = []

    class BlockingBudget:
        def reserve(self, invocation):
            started.set()
            try:
                assert release.wait(2)
                guard.reserve(invocation)
            finally:
                finished.set()

    async def fetch():
        calls.append(1)
        return evidence()

    async def run():
        compiler = ContextCompiler(
            load_agent_catalog(),
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
            call_budget=BlockingBudget(),
            overall_timeout_seconds=0.05,
        )
        task = asyncio.create_task(
            compiler.compile("A02", "0.1.0", task_id="interrupted", scope="PAPER")
        )
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.001)
            assert started.is_set()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.inputs[0].status == "TIMEOUT"
            assert calls == []
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 1)

    asyncio.run(run())
    assert calls == []
    assert guard.status()["agents"][1]["daily_calls"] == 1


def test_source_reservation_binds_task_input_class_and_scope_without_payload_text(tmp_path):
    path = tmp_path / "calls.sqlite3"
    guard = AgentCallBudget(path, policy=policy(), clock=lambda: NOW)

    async def fetch():
        return evidence()

    asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            {"world_state": SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)},
            clock=lambda: NOW,
            call_budget=guard,
        ).compile("A02", "0.1.0", task_id="bound-source", scope="PAPER")
    )
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT body FROM agent_call_reservations").fetchone()[0]
    request = {
        "agent_id": "A02",
        "agent_version": "0.1.0",
        "tool": "WORLD_STATE_READ",
        "scope": "PAPER",
        "payload": {
            "purpose": "CONTEXT_SOURCE_FETCH",
            "input_name": "world_state",
            "resource_class": "GENERAL",
        },
        "task_id": "bound-source",
        "context_ref": None,
    }
    assert (
        json.loads(raw)["request_sha256"]
        == sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert b"observation" not in raw and b"bound-source" not in raw


def test_corrupt_retained_budget_withholds_context_after_configuration_removed(tmp_path):
    path = tmp_path / "paper.sqlite3"
    headers = {"X-Operator-Token": "operator"}
    create_app(
        paper_db_path=path, operator_token="operator", clock=lambda: NOW, agent_call_policy=policy()
    )
    (tmp_path / "paper.agent-calls.sqlite3.pin.json").write_bytes(b"corrupt")
    with TestClient(
        create_app(paper_db_path=path, operator_token="operator", clock=lambda: NOW)
    ) as client:
        result = client.post(
            "/api/v1/paper/agents/context",
            headers=headers,
            json={"agent_id": "A02", "agent_version": "0.1.0", "task_id": "corrupt-archive"},
        )
        assert result.status_code == 200
        assert result.json()["input_refs"] == []
        assert all(row["status"] in {"DENIED", "MISSING"} for row in result.json()["inputs"])
        assert (
            client.get("/api/v1/agents/call-budget", headers=headers).json()["state"]
            == "UNAVAILABLE"
        )


def test_research_context_uses_same_quota_before_declared_training_fetch(tmp_path):
    with TestClient(
        create_app(
            paper_db_path=tmp_path / "paper.sqlite3",
            operator_token="operator",
            clock=lambda: NOW,
            agent_call_policy=exhausted_role_policy("A08"),
        )
    ) as client:
        result = client.post(
            "/api/v1/sandbox/research/context",
            headers={"X-Operator-Token": "operator"},
            json={"task_id": "research-quota"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["input_refs"] == []
        assert all(row["status"] in {"DENIED", "MISSING"} for row in result.json()["inputs"])


def test_builder_context_uses_same_quota_for_reviewed_inert_candidate(tmp_path):
    from test_research_hypothesis_api import NOW as RESEARCH_NOW, HEADERS
    from test_strategy_builder_api import review

    with TestClient(
        create_app(
            paper_db_path=tmp_path / "paper.sqlite3",
            operator_token="fixture-hypothesis",
            clock=lambda: RESEARCH_NOW,
            agent_call_policy=exhausted_role_policy("A09"),
        )
    ) as client:
        reviewed = review(client)
        result = client.post(
            "/api/v1/sandbox/strategy-builder/context",
            headers=HEADERS,
            json={"task_id": "builder-quota", "review_ref": reviewed["review_ref"]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["input_refs"] == []
        assert all(row["status"] in {"DENIED", "MISSING"} for row in result.json()["inputs"])


def test_validation_context_exhaustion_cannot_restore_failed_candidate_evidence(package, tmp_path):
    from contextlib import ExitStack
    from ai_quant_os.candidate_validation_store import CandidateValidationStore
    from ai_quant_os.holdout_validator_rpc import HoldoutRpcClient, process_rpc_frame
    from ai_quant_os.holdout_validator_service import HoldoutRequestHandler
    from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore
    from ai_quant_os.research_store import ResearchStore
    from ai_quant_os.validation_package_store import ValidationPackageStore
    from test_candidate_validation_store import failed_tests
    from test_research_hypothesis_api import NOW as RESEARCH_NOW, HEADERS

    with ExitStack() as stack:
        holdouts = stack.enter_context(
            HoldoutEvidenceStore(tmp_path / "service.research-data.holdout.sqlite3")
        )
        packages = stack.enter_context(
            ValidationPackageStore(tmp_path / "service.validation-packages.sqlite3")
        )
        validations = stack.enter_context(
            CandidateValidationStore(tmp_path / "service.candidate-validations.sqlite3")
        )
        research = ResearchStore(tmp_path / "service.research.sqlite3")
        failure = validations.put_failure(package, failed_tests(package))
        handler = HoldoutRequestHandler(holdouts, packages, validations, research)

        class LocalServiceClient:
            def exchange(self, frame, *, timeout_ms):
                assert timeout_ms > 0
                return process_rpc_frame(frame, handler)

        validator_client = HoldoutRpcClient(LocalServiceClient())
        with TestClient(
            create_app(
                paper_db_path=tmp_path / "paper.sqlite3",
                operator_token="fixture-hypothesis",
                clock=lambda: RESEARCH_NOW,
                agent_call_policy=exhausted_role_policy("A10"),
                holdout_rpc_client=validator_client,
            )
        ) as client:
            result = client.post(
                "/api/v1/sandbox/validation-scientist/context",
                headers=HEADERS,
                json={"task_id": "validation-quota", "artifact_ref": failure.failure_ref},
            )
            assert result.status_code == 403, result.text
            assert result.json()["detail"] == "Validation context admission denied"
            status = client.get("/api/v1/agents/call-budget", headers=HEADERS).json()
            assert status["agents"][9]["daily_calls"] == 0
