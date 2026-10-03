"""Declared A18 development replay through protected API and immutable archives."""

import argparse
import asyncio
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Literal

from httpx import ASGITransport, AsyncClient
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.api import create_app
from ai_quant_os.health_contracts import HealthIncident
from ai_quant_os.health_store import HealthStore
from ai_quant_os.incident_agent import IncidentRecommendation, WorkflowProposal
from ai_quant_os.risk import SafetyState


class IncidentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    as_of: AwareDatetime
    code: Literal["QUOTE_STALE", "POSITION_MISMATCH"] | None
    future: bool = False
    expected_state: SafetyState
    expected_class: Literal["CRITICAL", "WARNING", "DEGRADED_EVIDENCE", "NO_NEW_INCIDENT_FOUND"]


class IncidentFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["DEVELOPMENT_API_REPLAY_WITH_SYNTHETIC_INCIDENTS_NOT_HOLDOUT"]
    cases: tuple[IncidentCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate incident case identity")
        return self


async def _replay_case(root: Path, case: IncidentCase) -> dict:
    if case.code:
        store = HealthStore(root / "paper.health.sqlite3")
        try:
            store.record(
                HealthIncident(
                    case.code,
                    "development-feed",
                    "{}",
                    case.as_of + timedelta(seconds=int(case.future)),
                )
            )
        finally:
            store._db.close()
    settings = dict(
        paper_db_path=root / "paper.sqlite3",
        operator_token="development-incident-fixture",
        clock=lambda: case.as_of,
        health_watchdog_enabled=False,
        daily_evidence_enabled=False,
        temporal_recovery_enabled=False,
    )
    headers = {"x-operator-token": "development-incident-fixture"}
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            before = (await client.get("/api/v1/system/status")).json()
            response = await client.post(
                "/api/v1/paper/agents/context",
                json={"agent_id": "A18", "agent_version": "0.2.0", "task_id": case.id},
            )
            response.raise_for_status()
            response = await client.post(
                "/api/v1/paper/agents/execute",
                json={"run_id": case.id, "context_ref": response.json()["context_ref"]},
            )
            response.raise_for_status()
            receipt = response.json()["receipt"]
            if receipt["status"] != "COMPLETED":
                return {
                    "case_id": case.id,
                    "matches": False,
                    "status": receipt["status"],
                    "restart_exact": False,
                    "controls_unchanged": False,
                    "output": None,
                    "proposal": None,
                }
            output = IncidentRecommendation.model_validate_json(receipt["output_json"])
            response = await client.get("/api/v1/paper/incidents/proposals/" + output.proposal_ref)
            response.raise_for_status()
            archival = response.json()
            proposal = WorkflowProposal.model_validate(archival["proposal"])
            controls_unchanged = before == (await client.get("/api/v1/system/status")).json()
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            response = await client.get("/api/v1/agents/graph-runs/" + case.id)
            response.raise_for_status()
            restart_exact = response.json()["receipt"] == receipt
            response = await client.get("/api/v1/paper/incidents/proposals/" + output.proposal_ref)
            response.raise_for_status()
            restart_exact &= response.json() == archival
    return {
        "case_id": case.id,
        "status": receipt["status"],
        "restart_exact": restart_exact,
        "controls_unchanged": controls_unchanged,
        "matches": output.recommended_state == case.expected_state
        and output.incident_class == case.expected_class
        and restart_exact
        and controls_unchanged,
        "output": output.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
    }


def _owned_case(root: Path, case: IncidentCase) -> dict:
    # Reap this trusted application replay to release FastAPI's cached SQLite handles.
    # It supplies no isolation or execution authority for proposed candidate code.
    names = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "PYTHONPATH",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in names}
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.incident_evaluation",
            "--case-worker",
            str(root.resolve()),
        ],
        input=case.model_dump_json().encode(),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode or not 0 < len(result.stdout) <= 65536:
        raise ValueError("Trusted incident replay failed or exceeded output budget")
    return json.loads(result.stdout)


async def evaluate_incident_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Incident fixture exceeds byte budget")
    fixture = IncidentFixture.model_validate_json(raw)
    results = []
    for case in fixture.cases:
        with TemporaryDirectory() as temporary:
            results.append(await asyncio.to_thread(_owned_case, Path(temporary), case))
    return {
        "schema_version": "1.0",
        "agent_id": "A18",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "incident_policy.py",
                "incident_sources.py",
                "incident_agent.py",
                "incident_proposal_api.py",
                "incident_proposal_store.py",
            )
        },
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(results),
        "matching_cases": sum(row["matches"] for row in results),
        "calibration_status": "UNAVAILABLE",
        "promotion_approved": False,
        "results": results,
    }


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--case-worker":
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Incident replay input exceeds byte budget")
        case = IncidentCase.model_validate_json(raw)
        print(json.dumps(asyncio.run(_replay_case(Path(sys.argv[2]), case))))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_incident_fixture(args.fixture))
    raw = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
    else:
        print(raw)
    if report["matching_cases"] != report["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
