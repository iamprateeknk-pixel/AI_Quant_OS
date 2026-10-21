"""The optional repair review is wired through the authenticated PAPER API."""

import asyncio
import base64
import json
from datetime import datetime, timezone
from hashlib import sha256
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytest.importorskip("temporalio")
from temporalio.testing import WorkflowEnvironment

from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore
from ai_quant_os.repair_containers import RepairExecutionReceipt
from ai_quant_os.repair_snapshots import canonical_json
from ai_quant_os.repair_workspace import RepairWorkspace
from test_temporal_recovery import temporal_binary


def signed_trust_store(tmp_path):
    root = Ed25519PrivateKey.generate()
    reviewer = Ed25519PrivateKey.generate()
    store = {
        "schema_version": "1.0",
        "signers": [
            {
                "verifier_id": "repair-reviewer",
                "public_key": base64.urlsafe_b64encode(reviewer.public_key().public_bytes_raw())
                .decode()
                .rstrip("="),
                "verifier_source_sha256": "e" * 64,
            }
        ],
    }
    store_payload = json.dumps(store, sort_keys=True, separators=(",", ":")).encode()
    raw = json.dumps(
        {
            "store": store,
            "signature": base64.urlsafe_b64encode(root.sign(store_payload)).decode().rstrip("="),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path = tmp_path / "repair-reviewers.json"
    path.write_bytes(raw)
    return path, root.public_key().public_bytes_raw(), reviewer


def signed_certificate(
    snapshot,
    reviewer,
    *,
    incident_id,
    request_sha256,
    replay_sha256,
    suite_sha256,
    image_id,
    verdict="PASS",
):
    certificate = {
        "schema_version": "1.0",
        "verifier_id": "repair-reviewer",
        "verifier_source_sha256": "e" * 64,
        "suite_sha256": suite_sha256,
        "image_id": image_id,
        "candidate_id": snapshot.candidate_id,
        "candidate_commit": snapshot.candidate_commit,
        "rollback_commit": snapshot.rollback_commit,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "request_sha256": request_sha256,
        "incident_id": incident_id,
        "agent_id": snapshot.proposed_manifest["agent_id"],
        "old_version": snapshot.incident["agent_version"],
        "new_version": snapshot.proposed_manifest["agent_version"],
        "replay_sha256": replay_sha256,
        "issued_at": "2026-10-14T12:30:00Z",
        "verdict": verdict,
        "test_results": [
            {
                "test_id": "incident-replay",
                "status": "FAIL" if verdict == "FAIL" else "PASS",
                "evidence_sha256": "3" * 64,
            },
            {"test_id": "regression-contract", "status": "PASS", "evidence_sha256": "4" * 64},
        ],
        "promotion_allowed": False,
    }
    payload = json.dumps(certificate, sort_keys=True, separators=(",", ":")).encode()
    envelope = {
        "certificate": certificate,
        "signature": base64.urlsafe_b64encode(reviewer.sign(payload)).decode().rstrip("="),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


def repository(tmp_path):
    root = tmp_path / "production"
    root.mkdir()

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Repair API fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    source = root / "src" / "ai_quant_os"
    source.mkdir(parents=True)
    (source / "regime_agent.py").write_text('VERSION = "0.2.0"\n', newline="\n")
    (source / "risk.py").write_text("LIMIT = 100\n", newline="\n")
    git("add", ".")
    git("commit", "-m", "Baseline")
    return root


def prepared_repair(tmp_path, repository_root, *, complete=True):
    paper_path = tmp_path / "paper.sqlite3"
    health = HealthStore(tmp_path / "paper.health.sqlite3")
    registry = load_agent_catalog()
    manifest = registry.get("A02", "0.2.0")
    incident = health.record(
        HealthIncident(
            "AGENT_FAILED",
            "agent-run-1",
            '{"failure":"fixture"}',
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "A02",
            "0.2.0",
            manifest.manifest_ref,
        )
    )
    registry.quarantine("A02", "0.2.0", manifest.manifest_ref)
    repair_root = tmp_path / "paper.repairs"
    workspace = RepairWorkspace(repository_root, repair_root, health, registry)
    candidate = workspace.prepare(
        incident.incident_id,
        new_version="0.2.1",
        replacements={"src/ai_quant_os/regime_agent.py": 'VERSION = "0.2.1"\n'},
        diagnosis="Prepared API integration fixture.",
        regression_plan=("Run the reviewed regression suite",),
    )
    snapshot = workspace.snapshot(candidate["candidate_id"])
    owner, container_id = "b" * 32, "c" * 64
    image_id, suite_sha256 = "sha256:" + "d" * 64, "e" * 64
    replay_sha256 = "2" * 64
    request_raw = canonical_json({"replay_sha256": replay_sha256}).encode()
    request_sha256 = sha256(request_raw).hexdigest()
    stdout, stderr = b"fixed verifier output", b""
    receipt = RepairExecutionReceipt(
        schema_version="1.0",
        owner=owner,
        container_id=container_id,
        image_id=image_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        suite_sha256=suite_sha256,
        request_sha256=request_sha256,
        state="EXECUTION_FINISHED_AWAITING_INDEPENDENT_VERIFICATION",
        exit_code=0,
        oom_killed=False,
        stdout_bytes=len(stdout),
        stdout_sha256=sha256(stdout).hexdigest(),
        stderr_bytes=len(stderr),
        stderr_sha256=sha256(stderr).hexdigest(),
    )
    jobs = repair_root / "jobs"
    jobs.mkdir(parents=True)
    prefix = jobs / f"repair-execution-{owner}"
    intent = {
        "schema_version": "1.0",
        "owner": owner,
        "container_id": container_id,
        "image_id": image_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "suite_sha256": suite_sha256,
        "request_sha256": request_sha256,
        "arguments": ["container", "start", "--attach", container_id],
        "timeout_seconds": 45,
        "output_limit": 65536,
        "state": "START_REQUEST_MAY_HAVE_BEEN_SENT",
        "execution_verified": False,
        "promotion_allowed": False,
    }
    prefix.with_suffix(".start-intent.json").write_text(canonical_json(intent), encoding="utf-8")
    prefix.with_suffix(".request.json").write_bytes(request_raw)
    artifacts = {
        "prefix": prefix,
        "stdout": stdout,
        "stderr": stderr,
        "receipt": receipt,
        "snapshot": snapshot,
        "request_sha256": request_sha256,
        "replay_sha256": replay_sha256,
        "suite_sha256": suite_sha256,
        "image_id": image_id,
    }
    if complete:
        artifacts["prefix"].with_suffix(".stdout").write_bytes(stdout)
        artifacts["prefix"].with_suffix(".stderr").write_bytes(stderr)
        artifacts["prefix"].with_suffix(".result.json").write_text(
            canonical_json(receipt.__dict__), encoding="utf-8"
        )
    return paper_path, incident, candidate, owner, artifacts


def test_optional_temporal_repair_api_starts_and_views_bound_review(tmp_path):
    binary = temporal_binary()
    repository_root = repository(tmp_path)
    paper_path, incident, candidate, owner, _ = prepared_repair(tmp_path, repository_root)
    trust_path, root_public_key, _ = signed_trust_store(tmp_path)

    async def run():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            target = environment.client.service_client.config.target_host
            options = {
                "operator_token": "operator",
                "paper_db_path": paper_path,
                "health_watchdog_enabled": True,
                "temporal_recovery_enabled": True,
                "temporal_recovery_target": target,
                "temporal_repair_enabled": True,
                "temporal_repair_repository": repository_root,
                "temporal_repair_trust_store": trust_path,
                "temporal_repair_trust_root_public_key": root_public_key,
            }
            headers = {"X-Operator-Token": "operator"}
            path = "/api/v1/repair/recovery"
            payload = {
                "incident_id": incident.incident_id,
                "candidate_id": candidate["candidate_id"],
                "execution_owner": owner,
                "run_id": "api-run-1",
            }

            def exercise():
                from ai_quant_os.api import create_app

                app = create_app(**options)
                with TestClient(app) as client:
                    assert client.get(path).status_code == 401
                    status_response = client.get(path, headers=headers)
                    assert status_response.headers["cache-control"] == "no-store"
                    assert client.get(path, headers=headers).json()["state"] == "READY"
                    assert (
                        client.post(
                            path, json={**payload, "promotion_allowed": True}, headers=headers
                        ).status_code
                        == 422
                    )
                    response = client.post(path, json=payload, headers=headers)
                    assert response.status_code == 202, response.text
                    assert response.json()["promotion_allowed"] is False
                    assert response.json()["production_released"] is False
                    workflow_path = (
                        path + f"/{incident.incident_id}/{candidate['candidate_id']}/api-run-1"
                    )
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(workflow_path, headers=headers)
                        assert view.status_code == 200, view.text
                        assert view.headers["cache-control"] == "no-store"
                        if view.json()["stage"] == "AWAITING_INDEPENDENT_VERIFICATION":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert view.json()["state"] == "RUNNING"
                    assert view.json()["promotion_allowed"] is False
                    assert view.json()["production_released"] is False
                    assert client.post(path, json=payload, headers=headers).status_code == 409
                    certificate_path = workflow_path + "/verification-certificate"
                    assert client.post(certificate_path, content=b"bad").status_code == 401
                    assert (
                        client.post(
                            certificate_path,
                            content=b"x" * 65537,
                            headers=headers,
                        ).status_code
                        == 413
                    )
                    assert (
                        client.post(
                            certificate_path,
                            content=b"not a signed certificate",
                            headers=headers,
                        ).status_code
                        == 202
                    )
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(workflow_path, headers=headers)
                        if view.json()["state"] == "COMPLETED":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert view.json()["stage"] == "REVIEW_REQUIRED"
                assert app.state.recovery_runtime is None

                with TestClient(create_app(**options)) as client:
                    view = client.get(workflow_path, headers=headers)
                    assert view.status_code == 200
                    assert view.json()["state"] == "COMPLETED"
                    assert view.json()["stage"] == "REVIEW_REQUIRED"

            await asyncio.to_thread(exercise)

    asyncio.run(run())


def test_temporal_repair_api_signals_only_a_waiting_execution_checkpoint(tmp_path):
    binary = temporal_binary()
    repository_root = repository(tmp_path)
    paper_path, incident, candidate, owner, artifacts = prepared_repair(
        tmp_path, repository_root, complete=False
    )
    trust_path, root_public_key, _ = signed_trust_store(tmp_path)

    async def run():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            options = {
                "operator_token": "operator",
                "paper_db_path": paper_path,
                "health_watchdog_enabled": True,
                "temporal_recovery_enabled": True,
                "temporal_recovery_target": environment.client.service_client.config.target_host,
                "temporal_repair_enabled": True,
                "temporal_repair_repository": repository_root,
                "temporal_repair_trust_store": trust_path,
                "temporal_repair_trust_root_public_key": root_public_key,
            }
            headers = {"X-Operator-Token": "operator"}
            path = "/api/v1/repair/recovery"
            payload = {
                "incident_id": incident.incident_id,
                "candidate_id": candidate["candidate_id"],
                "execution_owner": owner,
                "run_id": "uncertain-1",
            }
            workflow_path = (
                path + f"/{incident.incident_id}/{candidate['candidate_id']}/uncertain-1"
            )

            def exercise():
                from ai_quant_os.api import create_app

                app = create_app(**options)
                with TestClient(app) as client:
                    assert client.post(path, json=payload, headers=headers).status_code == 202
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(workflow_path, headers=headers)
                        assert view.status_code == 200, view.text
                        if view.json()["stage"] == "WAIT_FOR_PINNED_RECONCILIATION":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    prefix = artifacts["prefix"]
                    prefix.with_suffix(".stdout").write_bytes(artifacts["stdout"])
                    prefix.with_suffix(".stderr").write_bytes(artifacts["stderr"])
                    prefix.with_suffix(".result.json").write_text(
                        canonical_json(artifacts["receipt"].__dict__), encoding="utf-8"
                    )
                    assert (
                        client.post(workflow_path + "/reconcile", headers=headers).status_code
                        == 202
                    )
                    deadline = time.monotonic() + 20
                    while True:
                        view = client.get(workflow_path, headers=headers)
                        assert view.status_code == 200, view.text
                        if view.json()["stage"] == "AWAITING_INDEPENDENT_VERIFICATION":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert view.json()["state"] == "RUNNING"
                    assert view.json()["stage"] == "AWAITING_INDEPENDENT_VERIFICATION"
                    assert view.json()["promotion_allowed"] is False
                    assert view.json()["production_released"] is False
                    assert (
                        client.post(workflow_path + "/reconcile", headers=headers).status_code
                        == 409
                    )

            await asyncio.to_thread(exercise)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("verdict", "expected_stage"),
    [
        ("PASS", "INDEPENDENT_VERIFICATION_PASSED"),
        ("FAIL", "REPAIR_FAILED_AWAITING_REVIEW"),
    ],
)
def test_signed_certificate_outcome_flows_through_api_and_verifier_activity(
    tmp_path, verdict, expected_stage
):
    binary = temporal_binary()
    repository_root = repository(tmp_path)
    paper_path, incident, candidate, owner, artifacts = prepared_repair(tmp_path, repository_root)
    trust_path, root_public_key, reviewer = signed_trust_store(tmp_path)
    certificate = signed_certificate(
        artifacts["snapshot"],
        reviewer,
        incident_id=incident.incident_id,
        request_sha256=artifacts["request_sha256"],
        replay_sha256=artifacts["replay_sha256"],
        suite_sha256=artifacts["suite_sha256"],
        image_id=artifacts["image_id"],
        verdict=verdict,
    )

    async def run():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=binary,
            dev_server_log_level="error",
            ui=False,
        ) as environment:
            options = {
                "operator_token": "operator",
                "paper_db_path": paper_path,
                "health_watchdog_enabled": True,
                "temporal_recovery_enabled": True,
                "temporal_recovery_target": environment.client.service_client.config.target_host,
                "temporal_repair_enabled": True,
                "temporal_repair_repository": repository_root,
                "temporal_repair_trust_store": trust_path,
                "temporal_repair_trust_root_public_key": root_public_key,
            }
            headers = {"X-Operator-Token": "operator"}
            base = "/api/v1/repair/recovery"
            run_id = "signed-pass-1"
            workflow_path = base + f"/{incident.incident_id}/{candidate['candidate_id']}/{run_id}"

            def exercise():
                from ai_quant_os.api import create_app

                with TestClient(create_app(**options)) as client:
                    started = client.post(
                        base,
                        json={
                            "incident_id": incident.incident_id,
                            "candidate_id": candidate["candidate_id"],
                            "execution_owner": owner,
                            "run_id": run_id,
                        },
                        headers=headers,
                    )
                    assert started.status_code == 202, started.text
                    deadline = time.monotonic() + 20
                    while True:
                        current = client.get(workflow_path, headers=headers)
                        assert current.status_code == 200, current.text
                        if current.json()["stage"] == "AWAITING_INDEPENDENT_VERIFICATION":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    submitted = client.post(
                        workflow_path + "/verification-certificate",
                        content=certificate,
                        headers=headers,
                    )
                    assert submitted.status_code == 202, submitted.text
                    expected_hash = sha256(certificate).hexdigest()
                    assert submitted.json()["certificate_sha256"] == expected_hash
                    duplicate = client.post(
                        workflow_path + "/verification-certificate",
                        content=certificate,
                        headers=headers,
                    )
                    assert duplicate.status_code == 202, duplicate.text
                    conflict = client.post(
                        workflow_path + "/verification-certificate",
                        content=b"different certificate bytes",
                        headers=headers,
                    )
                    assert conflict.status_code == 409, conflict.text
                    deadline = time.monotonic() + 20
                    while True:
                        current = client.get(workflow_path, headers=headers)
                        assert current.status_code == 200, current.text
                        if current.json()["state"] == "COMPLETED":
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    result = current.json()["result"]
                    assert result["stage"] == expected_stage
                    assert result["certificate_sha256"] == expected_hash
                    assert result["promotion_allowed"] is False
                    assert result["production_released"] is False
                    assert client.get("/api/v1/risk").json()["safety_state"] == "NO_NEW_RISK"
                    health = client.get("/api/v1/paper/health", headers=headers).json()
                    assert health["open_incidents"] == 1
                    assert health["quarantined_versions"] == ["A02@0.2.0"]

            await asyncio.to_thread(exercise)

    asyncio.run(run())


def test_temporal_repair_requires_health_monitor_and_paper_temporal_runtime(tmp_path):
    from ai_quant_os.api import create_app

    with pytest.raises(ValueError, match="persistent health"):
        create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            temporal_repair_enabled=True,
            temporal_recovery_enabled=False,
            health_watchdog_enabled=False,
        )


def test_temporal_repair_refuses_to_start_without_root_signed_reviewer_registry(
    tmp_path, monkeypatch
):
    from ai_quant_os.api import create_app

    monkeypatch.delenv("AI_QUANT_REPAIR_VERIFIER_TRUST_STORE", raising=False)
    monkeypatch.delenv("AI_QUANT_REPAIR_TRUST_ROOT_PUBLIC_KEY", raising=False)
    repository_root = repository(tmp_path)

    with pytest.raises(ValueError, match="trusted verifier registry"):
        create_app(
            operator_token="operator",
            paper_db_path=tmp_path / "paper.sqlite3",
            health_watchdog_enabled=True,
            temporal_recovery_enabled=True,
            temporal_recovery_target="127.0.0.1:7233",
            temporal_repair_enabled=True,
            temporal_repair_repository=repository_root,
        )
