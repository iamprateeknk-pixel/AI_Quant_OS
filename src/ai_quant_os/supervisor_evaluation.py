"""Declared A01 development replay through the actual protected API and persisted graphs."""

import argparse
import asyncio
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from httpx import ASGITransport, AsyncClient
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_quant_os.api import create_app
from ai_quant_os.event_feed import EventFeed
from ai_quant_os.event_feed_store import EventFeedStore
from ai_quant_os.graph_ledger import RunStatus
from ai_quant_os.rate_archive import RateArchive
from ai_quant_os.supervisor_agent import TaskPlan
from ai_quant_os.supervisor_sources import SPECIALISTS


class SupervisorCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    as_of: AwareDatetime
    market: Literal["EMPTY", "FRESH", "STALE"]
    rates: Literal["MISSING", "LOCAL_CSV_IMPORT", "CORRUPT_LOCAL_ARCHIVE"]
    feed: EventFeed | None = None
    expected_statuses: dict[str, RunStatus | Literal["NOT_STARTED_SOURCE"]]
    expected_rate_status: Literal["MISSING", "RECENT_RECEIPT"] | None
    expected_event_status: Literal["MISSING", "FRESH"] | None

    @model_validator(mode="after")
    def exact_specialists(self):
        if set(self.expected_statuses) != set(SPECIALISTS):
            raise ValueError("Supervisor expectations must cover all six specialists")
        return self


class SupervisorFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal[
        "DEVELOPMENT_API_REPLAY_WITH_SYNTHETIC_WORLD_EVENTS_AND_LOCAL_CSV_IMPORT_NOT_HOLDOUT"
    ]
    rate_csv: str = Field(min_length=1, max_length=65536)
    cases: tuple[SupervisorCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate supervisor case identity")
        return self


def _prepare_case(root: Path, case: SupervisorCase, raw: bytes) -> None:
    if case.rates != "MISSING":
        with RateArchive(root / "rates.sqlite3") as archive:
            archive.publish(raw, received_at=case.as_of, capture_mode="LOCAL_CSV_IMPORT")
        if case.rates == "CORRUPT_LOCAL_ARCHIVE":
            with closing(sqlite3.connect(root / "rates.sqlite3")) as db, db:
                db.execute(
                    "UPDATE rate_captures SET raw_csv=?", (b"development-corruption-fixture",)
                )
    if case.feed:
        with EventFeedStore(root / "paper.events.sqlite3") as archive:
            archive.put(case.feed, now=case.as_of)


async def _publish_market(client: AsyncClient, case: SupervisorCase) -> None:
    if case.market == "EMPTY":
        return
    bars = [
        {
            "instrument_id": "EURUSD",
            "observed_at": (case.as_of - timedelta(seconds=2 - i)).isoformat(),
            "open": str(1.1 + i / 10000),
            "high": str(1.1 + i / 10000),
            "low": str(1.1 + i / 10000),
            "close": str(1.1 + i / 10000),
            "volume": "1",
        }
        for i in range(3)
    ]
    response = await client.post("/api/v1/paper/world-state/bars", json={"bars": bars})
    response.raise_for_status()
    response = await client.post(
        "/api/v1/paper/quotes",
        json={
            "instrument_id": "EURUSD",
            "bid": "1.1002",
            "ask": "1.1003",
            "observed_at": (
                case.as_of - timedelta(seconds=10) if case.market == "STALE" else case.as_of
            ).isoformat(),
            "world_state_version": "supervisor-development",
        },
    )
    response.raise_for_status()


async def _replay_case(root: Path, case: SupervisorCase) -> dict:
    settings = dict(
        paper_db_path=root / "paper.sqlite3",
        operator_token="development-supervisor-fixture",
        clock=lambda: case.as_of,
        health_watchdog_enabled=False,
        daily_evidence_enabled=False,
        temporal_recovery_enabled=False,
    )
    app = create_app(**settings)
    headers = {"x-operator-token": "development-supervisor-fixture"}
    archived = {}
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            await _publish_market(client, case)
            started = perf_counter()
            response = await client.post(
                "/api/v1/paper/agents/context",
                json={
                    "agent_id": "A01",
                    "agent_version": "0.2.0",
                    "task_id": case.id,
                },
            )
            response.raise_for_status()
            context_ref = response.json()["context_ref"]
            response = await client.post(
                "/api/v1/paper/agents/execute",
                json={
                    "run_id": case.id,
                    "context_ref": context_ref,
                },
            )
            response.raise_for_status()
            receipt = response.json()["receipt"]
            elapsed = (perf_counter() - started) * 1000
            if receipt["status"] != "COMPLETED":
                return {
                    "case_id": case.id,
                    "matches": False,
                    "status": receipt["status"],
                    "output": None,
                    "restart_exact": False,
                    "elapsed_ms": elapsed,
                }
            output = TaskPlan.model_validate_json(receipt["output_json"])
            archived[case.id] = receipt
            macro, event = None, None
            for call in output.calls:
                if call.context_ref is None:
                    continue
                response = await client.get("/api/v1/agents/graph-runs/" + call.run_id)
                response.raise_for_status()
                child = response.json()["receipt"]
                archived[call.run_id] = child
                if call.agent_id == "A03" and child["output_json"]:
                    macro = json.loads(child["output_json"])["rate_receipt_status"]
                if call.agent_id == "A06" and child["output_json"]:
                    event = json.loads(child["output_json"])["feed_status"]
    restart_exact = True
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            for run_id, original in archived.items():
                response = await client.get("/api/v1/agents/graph-runs/" + run_id)
                response.raise_for_status()
                restart_exact &= response.json()["receipt"] == original
    statuses = {call.agent_id: call.status for call in output.calls}
    return {
        "case_id": case.id,
        "status": receipt["status"],
        "elapsed_ms": elapsed,
        "context_ref": context_ref,
        "matches": statuses == case.expected_statuses
        and macro == case.expected_rate_status
        and event == case.expected_event_status
        and restart_exact,
        "restart_exact": restart_exact,
        "actual_statuses": statuses,
        "actual_rate_status": macro,
        "actual_event_status": event,
        "output": output.model_dump(mode="json"),
    }


async def evaluate_supervisor_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Supervisor fixture exceeds byte budget")
    fixture = SupervisorFixture.model_validate_json(raw)
    results = []
    for case in fixture.cases:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _prepare_case(root, case, fixture.rate_csv.encode())
            results.append(await asyncio.to_thread(_isolated_case, root, case))
    return {
        "schema_version": "1.0",
        "agent_id": "A01",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("supervisor_sources.py", "supervisor_agent.py", "supervisor_dispatch.py")
        },
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(results),
        "matching_cases": sum(row["matches"] for row in results),
        "calibration_status": "UNAVAILABLE",
        "promotion_approved": False,
        "results": results,
    }


def _isolated_case(root: Path, case: SupervisorCase) -> dict:
    # FastAPI caches retain route closures and their SQLite handles. A reaped,
    # trusted replay process releases them before this fixture root is removed.
    # This is test process ownership, not isolation for candidate code.
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
            "ai_quant_os.supervisor_evaluation",
            "--case-worker",
            str(root.resolve()),
        ],
        input=case.model_dump_json().encode(),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode or not 0 < len(result.stdout) <= 65536:
        raise ValueError("Trusted supervisor replay worker failed or exceeded output budget")
    return json.loads(result.stdout)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--case-worker":
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Supervisor case worker input exceeds budget")
        case = SupervisorCase.model_validate_json(raw)
        print(json.dumps(asyncio.run(_replay_case(Path(sys.argv[2]), case))))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_supervisor_fixture(args.fixture))
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
