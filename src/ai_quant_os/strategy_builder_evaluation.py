"""Trusted development artifact replay; proposed candidate code and tests are never run."""

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
from ai_quant_os.strategy_builder_agent import StrategyCandidate
from ai_quant_os.strategy_candidate_contracts import CandidateBundle


class BuilderCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    as_of: AwareDatetime
    closes: tuple[Decimal, ...] = Field(min_length=12, max_length=60)
    expected_rule: ResearchRule


class BuilderFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    provenance: Literal[
        "SYNTHETIC_OPERATOR_REVIEW_AND_INERT_ARTIFACT_REPLAY_NOT_STRATEGY_VALIDATION"
    ]
    cases: tuple[BuilderCase, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate builder case identity")
        return self


async def _replay_case(root: Path, case: BuilderCase) -> dict:
    settings = dict(
        paper_db_path=root / "paper.sqlite3",
        operator_token="development-builder-fixture",
        clock=lambda: case.as_of,
        health_watchdog_enabled=False,
        daily_evidence_enabled=False,
        temporal_recovery_enabled=False,
    )
    headers = {"x-operator-token": "development-builder-fixture"}
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            before = (await client.get("/api/v1/system/status")).json()
            csv = "instrument_id,observed_at,open,high,low,close,volume\n" + "".join(
                f"EURUSD,2026-10-01T00:{i:02}:00Z,{price},{price},{price},{price},1\n"
                for i, price in enumerate(case.closes)
            )
            await _post(
                client,
                "/api/v1/paper/research/datasets",
                {"source_id": case.id, "resource_class": "TRAINING", "csv": csv},
            )
            context = await _post(client, "/api/v1/sandbox/research/context", {"task_id": case.id})
            response = await _post(
                client,
                "/api/v1/sandbox/research/execute",
                {"run_id": case.id, "context_ref": context["context_ref"]},
            )
            hypothesis = HypothesisSpec.model_validate_json(
                response["receipt"]["output_json"]
            ).hypotheses[0]
            review = await _post(
                client,
                "/api/v1/sandbox/research/reviews",
                {
                    "review_id": case.id,
                    "run_id": case.id,
                    "hypothesis_signature": hypothesis.signature,
                    "rationale": "Declared synthetic development construction review; no alpha acceptance.",
                },
            )
            reviewed = review["review"]
            context = await _post(
                client,
                "/api/v1/sandbox/strategy-builder/context",
                {"task_id": case.id + "-build", "review_ref": reviewed["review_ref"]},
            )
            response = await _post(
                client,
                "/api/v1/sandbox/strategy-builder/execute",
                {"run_id": case.id + "-build", "context_ref": context["context_ref"]},
            )
            receipt = response["receipt"]
            output = StrategyCandidate.model_validate_json(receipt["output_json"])
            candidate = await _get(
                client, "/api/v1/sandbox/strategy-builder/candidates/" + output.candidate_ref
            )
            bundle = CandidateBundle.model_validate(candidate["candidate"])
            controls_unchanged = before == (await client.get("/api/v1/system/status")).json()
    app = create_app(**settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://local-fixture", headers=headers
        ) as client:
            saved = await _get(client, "/api/v1/agents/graph-runs/" + case.id + "-build")
            restart_exact = saved["receipt"] == receipt
            restart_exact &= (
                await _get(
                    client, "/api/v1/sandbox/strategy-builder/candidates/" + output.candidate_ref
                )
            ) == candidate
            saved_review = await _get(
                client, "/api/v1/sandbox/research/reviews/" + reviewed["review_ref"]
            )
            restart_exact &= saved_review["review"] == reviewed
    return {
        "case_id": case.id,
        "restart_exact": restart_exact,
        "controls_unchanged": controls_unchanged,
        "matches": hypothesis.rule == case.expected_rule
        and restart_exact
        and controls_unchanged
        and bundle.tests_status == "NOT_RUN"
        and not bundle.execution_authorized
        and not bundle.runtime_registered,
        "output": output.model_dump(mode="json"),
        "candidate": bundle.model_dump(mode="json"),
    }


async def _post(client: AsyncClient, path: str, payload: dict) -> dict:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


async def _get(client: AsyncClient, path: str) -> dict:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


def _owned_case(root: Path, case: BuilderCase) -> dict:
    # Trusted app replay releases Windows SQLite handles; it is not a candidate sandbox.
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
            "ai_quant_os.strategy_builder_evaluation",
            "--case-worker",
            str(root.resolve()),
        ],
        input=case.model_dump_json().encode(),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode or not 0 < len(result.stdout) <= 65536:
        raise ValueError("Trusted builder replay failed or exceeded output budget")
    return json.loads(result.stdout)


async def evaluate_builder_fixture(path: Path) -> dict:
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Builder fixture exceeds byte bound")
    fixture = BuilderFixture.model_validate_json(raw)
    results = []
    for case in fixture.cases:
        with TemporaryDirectory() as temporary:
            results.append(await asyncio.to_thread(_owned_case, Path(temporary), case))
    return {
        "schema_version": "1.0",
        "agent_id": "A09",
        "agent_version": "0.2.0",
        "fixture_sha256": sha256(raw).hexdigest(),
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "provenance": fixture.provenance,
        "source_pins": [
            {
                "path": name,
                "sha256": sha256(Path(__file__).with_name(name).read_bytes()).hexdigest(),
            }
            for name in (
                "hypothesis_review_contracts.py",
                "strategy_candidate_contracts.py",
                "strategy_builder.py",
                "strategy_build_store.py",
                "strategy_builder_sources.py",
                "strategy_builder_agent.py",
                "strategy_builder_service.py",
                "strategy_builder_api.py",
            )
        ],
        "case_count": len(results),
        "matching_cases": sum(row["matches"] for row in results),
        "proposed_code_executed": False,
        "isolation_verified": False,
        "promotion_approved": False,
        "results": results,
    }


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--case-worker":
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Builder replay input exceeds byte budget")
        print(
            json.dumps(
                asyncio.run(_replay_case(Path(sys.argv[2]), BuilderCase.model_validate_json(raw)))
            )
        )
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_builder_fixture(args.fixture))
    raw = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(raw)
    else:
        print(raw)
    if report["matching_cases"] != report["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
