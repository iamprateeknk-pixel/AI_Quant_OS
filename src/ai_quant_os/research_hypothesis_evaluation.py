"""Declared A08 development replay; no alpha, holdout quality or isolation claim."""

import argparse
import asyncio
from decimal import Decimal
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
from ai_quant_os.research_hypothesis_contracts import HypothesisSpec
from ai_quant_os.research_hypothesis_policy import ResearchRule


class HypothesisCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    as_of: AwareDatetime
    closes: tuple[Decimal, ...] = Field(min_length=0, max_length=60)
    resource_class: Literal["TRAINING", "HOLDOUT"] = "TRAINING"
    archive_spread: bool = False
    expected_status: Literal["PROPOSED", "WAIT_FOR_EVIDENCE"]
    expected_rule: ResearchRule | None

    @model_validator(mode="after")
    def valid_closes(self):
        if len(self.closes) == 1 or any(not p.is_finite() or not 0 < p < 100 for p in self.closes):
            raise ValueError("Invalid development close observations")
        return self


class HypothesisFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal["DEVELOPMENT_API_REPLAY_WITH_SYNTHETIC_BARS_NOT_HOLDOUT_QUALITY"]
    cases: tuple[HypothesisCase, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate hypothesis case identity")
        return self


async def _replay_case(root: Path, case: HypothesisCase) -> dict:
    settings = dict(
        paper_db_path=root / "paper.sqlite3",
        operator_token="development-hypothesis-fixture",
        clock=lambda: case.as_of,
        health_watchdog_enabled=False,
        daily_evidence_enabled=False,
        temporal_recovery_enabled=False,
    )
    headers = {"x-operator-token": "development-hypothesis-fixture"}
    app = create_app(**settings)
    dataset_ref = None
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            before = (await client.get("/api/v1/system/status")).json()
            if case.closes:
                csv = "instrument_id,observed_at,open,high,low,close,volume\n" + "".join(
                    f"EURUSD,2026-10-01T00:{index:02}:00Z,{price},{price},{price},{price},1\n"
                    for index, price in enumerate(case.closes)
                )
                response = await client.post(
                    "/api/v1/paper/research/datasets",
                    json={"source_id": case.id, "resource_class": case.resource_class, "csv": csv},
                )
                if case.resource_class == "HOLDOUT":
                    if response.status_code != 422:
                        raise ValueError("PAPER research route accepted HOLDOUT data")
                else:
                    response.raise_for_status()
                    dataset_ref = response.json()["receipt"]["dataset_ref"]
            if case.archive_spread:
                response = await client.post(
                    "/api/v1/paper/quotes",
                    json={
                        "instrument_id": "EURUSD",
                        "bid": "1.1",
                        "ask": "1.1001",
                        "observed_at": case.as_of.isoformat(),
                        "world_state_version": "development",
                    },
                )
                response.raise_for_status()
                response = await client.post("/api/v1/paper/world-state/publish", json={})
                response.raise_for_status()
            context, receipt = await _run(client, case.id)
            output = HypothesisSpec.model_validate_json(receipt["output_json"])
            holdout_excluded = case.resource_class != "HOLDOUT" or (
                dataset_ref is None and output.training_dataset_count == 0
            )
            controls_unchanged = before == (await client.get("/api/v1/system/status")).json()
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            response = await client.get("/api/v1/agents/graph-runs/" + case.id)
            response.raise_for_status()
            restart_exact = response.json()["receipt"] == receipt
            _, second_receipt = await _run(client, case.id + "-memory")
            second = HypothesisSpec.model_validate_json(second_receipt["output_json"])
            memory_suppressed = not second.hypotheses and second.already_proposed_count == len(
                output.hypotheses
            )
    rules = tuple(row.rule for row in output.hypotheses)
    costs_match = all(
        row.expected_costs.status
        == ("HISTORICAL_SPREAD_ONLY" if case.archive_spread else "UNAVAILABLE")
        for row in output.hypotheses
    )
    return {
        "case_id": case.id,
        "status": receipt["status"],
        "restart_exact": restart_exact,
        "controls_unchanged": controls_unchanged,
        "holdout_excluded": holdout_excluded,
        "memory_suppressed": memory_suppressed,
        "matches": output.status == case.expected_status
        and rules == ((case.expected_rule,) if case.expected_rule else ())
        and costs_match
        and restart_exact
        and controls_unchanged
        and holdout_excluded
        and memory_suppressed,
        "source_bindings": [
            {
                "input_name": row["input_name"],
                "source_tool": row["source_tool"],
                "resource_class": row["resource_class"],
            }
            for row in context["inputs"]
        ],
        "output": output.model_dump(mode="json"),
    }


async def _run(client: AsyncClient, task: str) -> tuple[dict, dict]:
    response = await client.post("/api/v1/sandbox/research/context", json={"task_id": task})
    response.raise_for_status()
    context = response.json()
    response = await client.post(
        "/api/v1/sandbox/research/execute",
        json={"run_id": task, "context_ref": context["context_ref"]},
    )
    response.raise_for_status()
    receipt = response.json()["receipt"]
    if receipt["status"] != "COMPLETED":
        raise ValueError("Development hypothesis graph did not complete")
    return context, receipt


def _owned_case(root: Path, case: HypothesisCase) -> dict:
    # Trusted app replay releases cached SQLite handles; this does not isolate candidate code.
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
            "ai_quant_os.research_hypothesis_evaluation",
            "--case-worker",
            str(root.resolve()),
        ],
        input=case.model_dump_json().encode(),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode or not 0 < len(result.stdout) <= 65536:
        raise ValueError("Trusted hypothesis replay failed or exceeded output budget")
    return json.loads(result.stdout)


async def evaluate_hypothesis_fixture(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Hypothesis fixture exceeds byte budget")
    fixture = HypothesisFixture.model_validate_json(raw)
    results = []
    for case in fixture.cases:
        with TemporaryDirectory() as temporary:
            results.append(await asyncio.to_thread(_owned_case, Path(temporary), case))
    return {
        "schema_version": "1.0",
        "agent_id": "A08",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "provenance": fixture.provenance,
        "source_pins": [
            {
                "path": name,
                "sha256": sha256(Path(__file__).with_name(name).read_bytes()).hexdigest(),
            }
            for name in (
                "research_data_contracts.py",
                "research_hypothesis_policy.py",
                "research_hypothesis_sources.py",
                "research_hypothesis_contracts.py",
                "research_hypothesis_agent.py",
                "research_hypothesis_api.py",
            )
        ],
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(results),
        "matching_cases": sum(row["matches"] for row in results),
        "calibration_status": "UNAVAILABLE",
        "promotion_approved": False,
        "isolation_verified": False,
        "results": results,
    }


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--case-worker":
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Hypothesis replay input exceeds byte budget")
        case = HypothesisCase.model_validate_json(raw)
        print(json.dumps(asyncio.run(_replay_case(Path(sys.argv[2]), case))))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_hypothesis_fixture(args.fixture))
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
