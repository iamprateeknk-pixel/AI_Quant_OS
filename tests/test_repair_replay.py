import asyncio
import importlib
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256

import pytest

from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
from ai_quant_os.agent_registry import AgentToolBoundary
from ai_quant_os.graph_runtime import GraphRuntime, AgentImplementation
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.regime_agent import RegimeDistribution
from ai_quant_os.repair_snapshots import canonical_json
from test_repair_workspace import NOW, prepare, workspace
from test_repair_workspace import repository as repository


def module():
    assert importlib.util.find_spec("ai_quant_os.repair_replay") is not None, (
        "Missing archived incident replay closure"
    )
    return importlib.import_module("ai_quant_os.repair_replay")


def failed_snapshot(tmp_path, repository):
    value, _, registry = workspace(tmp_path, repository)
    graph_path = tmp_path / "graph.sqlite3"
    # An independent registry starts the original invocation before it is quarantined.
    from ai_quant_os.agent_registry import load_agent_catalog

    runtime_registry = load_agent_catalog()

    async def run():
        async def fetch():
            return EvidenceRecord(
                source_id="original-fixture",
                as_of=NOW,
                valid_until=NOW + timedelta(seconds=4),
                trusted=True,
                payload_json='{"captured_value":7}',
            )

        context = await ContextCompiler(
            runtime_registry,
            {
                name: SourceBinding("WORLD_STATE_READ", "GENERAL", fetch)
                for name in runtime_registry.get("A02", "0.2.0").required_inputs
            },
            clock=lambda: NOW,
        ).compile("A02", "0.2.0", task_id="original-task", scope="PAPER")

        async def fail(invocation):
            raise RuntimeError("intentional original invocation failure")

        implementation = AgentImplementation(
            registry.get("A02", "0.2.0").manifest_ref,
            RegimeDistribution,
            fail,
        )
        async with GraphRuntime.open(
            graph_path,
            registry=runtime_registry,
            implementations={("A02", "0.2.0"): implementation},
            tools=AgentToolBoundary(runtime_registry, {}),
            clock=lambda: NOW,
        ) as runtime:
            return context, await runtime.run("original-failed", context)

    context, receipt = asyncio.run(run())
    assert receipt.status == "FAILED"
    incident = value.health.record(
        HealthIncident(
            "AGENT_FAILED",
            receipt.run_id,
            canonical_json(json.loads(receipt.model_dump_json())),
            NOW,
            receipt.agent_id,
            receipt.agent_version,
            receipt.manifest_ref,
        )
    )
    candidate = prepare(value, incident)
    return value.snapshot(candidate["candidate_id"]), graph_path, context


def test_actual_failed_graph_inputs_are_read_only_and_preserve_original_expiry(
    tmp_path, repository
):
    snapshot, path, context = failed_snapshot(tmp_path, repository)
    before = sha256(path.read_bytes()).hexdigest()
    evidence = module().load_incident_replay(snapshot, path)
    assert evidence.record.checkpoint.context == context
    assert evidence.record.receipt.status == "FAILED"
    assert evidence.record.checkpoint.context.valid_until == NOW + timedelta(seconds=4)
    assert all(
        row.payload_json == '{"captured_value":7}'
        for row in evidence.record.checkpoint.context.inputs
    )
    assert evidence.snapshot_sha256 == snapshot.snapshot_sha256
    assert evidence.promotion_allowed is False
    assert evidence.kind == "ARCHIVED_INLINE_INPUTS"
    assert len(evidence.replay_sha256) == 64
    assert sha256(path.read_bytes()).hexdigest() == before


def test_missing_or_empty_graph_archive_is_not_bootstrapped(tmp_path, repository):
    snapshot, _, _ = failed_snapshot(tmp_path, repository)
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError, match="archive"):
        module().load_incident_replay(snapshot, missing)
    assert not missing.exists()
    missing.write_bytes(b"")
    with pytest.raises(ValueError, match="archive"):
        module().load_incident_replay(snapshot, missing)
    assert missing.read_bytes() == b""


def test_changed_receipt_or_incomplete_health_evidence_cannot_bind_another_context(
    tmp_path, repository
):
    snapshot, path, _ = failed_snapshot(tmp_path, repository)
    incident = snapshot.incident
    altered = json.loads(incident["evidence_json"])
    altered["task_id"] = "different-task"
    incident["evidence_json"] = canonical_json(altered)
    bad = replace(snapshot, incident_json=canonical_json(incident))
    with pytest.raises(ValueError, match="binding"):
        module().load_incident_replay(bad, path)
    incident["evidence_json"] = '{"run_id":"original-failed"}'
    incomplete = replace(snapshot, incident_json=canonical_json(incident))
    with pytest.raises(ValueError):
        module().load_incident_replay(incomplete, path)


def test_graph_archive_rehashed_corruption_is_denied_by_its_audit(tmp_path, repository):
    snapshot, path, _ = failed_snapshot(tmp_path, repository)
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT record_json FROM graph_runs").fetchone()[0]
        changed = raw.replace("captured_value", "altered_value")
        connection.execute(
            "UPDATE graph_runs SET record_json=?,record_hash=?",
            (changed, sha256(changed.encode()).hexdigest()),
        )
    with pytest.raises(ValueError, match="archive"):
        module().load_incident_replay(snapshot, path)


def test_replay_is_staged_as_a_separate_bound_record_and_foreign_snapshot_is_denied(
    tmp_path, repository
):
    from ai_quant_os.repair_jobs import prepared_repair_job

    snapshot, path, context = failed_snapshot(tmp_path, repository)
    replay = module().load_incident_replay(snapshot, path)
    program = b'raise RuntimeError("DO_NOT_EXECUTE")\n'
    with prepared_repair_job(
        snapshot,
        program,
        suite_sha256=sha256(program).hexdigest(),
        root=tmp_path / "jobs",
        replay=replay,
    ) as job:
        request = json.loads((job.path / "request.json").read_bytes())
        assert request["schema_version"] == "1.1"
        assert request["replay_sha256"] == replay.replay_sha256
        assert request["record_sha256"] == replay.record_sha256
        assert (job.path / "incident-record.json").read_text(encoding="utf-8") == replay.record_json
        assert replay.record.checkpoint.context == context
        job.verify()
    foreign = replace(replay, snapshot_sha256="0" * 64)
    rejected_root = tmp_path / "foreign-jobs"
    with pytest.raises(ValueError, match="replay"):
        with prepared_repair_job(
            snapshot,
            program,
            suite_sha256=sha256(program).hexdigest(),
            root=rejected_root,
            replay=foreign,
        ):
            pytest.fail("Foreign replay admitted")
    assert not rejected_root.exists()


def test_cli_reads_replay_metadata_without_exposing_captured_inputs(tmp_path, repository):
    from test_repair_cli import cli

    snapshot, path, _ = failed_snapshot(tmp_path, repository)
    result = cli(repository, tmp_path, "replay", snapshot.candidate_id, "--graph-store", str(path))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    replay = module().load_incident_replay(snapshot, path)
    assert payload["replay_sha256"] == replay.replay_sha256
    assert payload["record_sha256"] == replay.record_sha256
    assert payload["state"] == "ARCHIVED_INPUTS_ONLY"
    assert payload["promotion_allowed"] is False
    assert "captured_value" not in result.stdout
    missing = tmp_path / "missing.sqlite3"
    denied = cli(
        repository, tmp_path, "replay", snapshot.candidate_id, "--graph-store", str(missing)
    )
    assert denied.returncode == 2
    assert not missing.exists()
