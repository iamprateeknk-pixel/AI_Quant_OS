"""A04 must run in the real advisory graph with retained, bounded evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture-strength"}


def publish(client, rows, *, offsets=None):
    bars = []
    for identifier, prices in rows.items():
        for index, price in enumerate(prices):
            value = str(price)
            bars.append(
                {
                    "instrument_id": identifier,
                    "observed_at": (
                        NOW
                        - timedelta(
                            seconds=len(prices) - index - 1 + (offsets or {}).get(identifier, 0)
                        )
                    ).isoformat(),
                    "open": value,
                    "high": value,
                    "low": value,
                    "close": value,
                    "volume": "1",
                }
            )
    response = client.post("/api/v1/paper/world-state/bars", headers=HEADERS, json={"bars": bars})
    assert response.status_code == 200, response.text
    for identifier, prices in rows.items():
        response = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={
                "instrument_id": identifier,
                "bid": str(prices[-1]),
                "ask": str(prices[-1]),
                "observed_at": NOW.isoformat(),
                "world_state_version": "fixture",
            },
        )
        assert response.status_code == 200, response.text


def execute(client, identifier="strength-1"):
    response = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={
            "agent_id": "A04",
            "agent_version": "0.2.0",
            "task_id": "strength-task",
        },
    )
    assert response.status_code == 200, response.text
    context = response.json()
    response = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={
            "run_id": identifier,
            "context_ref": context["context_ref"],
        },
    )
    assert response.status_code == 200, response.text
    return context, response.json()["receipt"]


def test_actual_graph_returns_source_bound_vector_and_restart_retains_receipt(tmp_path):
    arguments = dict(
        operator_token="fixture-strength",
        clock=lambda: NOW,
        paper_db_path=tmp_path / "paper.sqlite3",
    )
    with TestClient(create_app(**arguments)) as client:
        publish(
            client,
            {
                "EURUSD": [1, "1.01", "1.02"],
                "USDJPY": [100, 101, 102],
                "EURJPY": [100, "102.01", "104.04"],
                "GBPUSD": ["1.2"] * 3,
            },
        )
        context, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["agent_id"] == "A04" and output["agent_version"] == "0.2.0"
        assert output["input_refs"] == output["evidence_refs"] == context["input_refs"]
        assert output["valid_until"] == context["valid_until"]
        assert output["requires_escalation"] is True
        assert output["macro_status"] == output["calibration_status"] == "UNAVAILABLE"
        assert Decimal(output["confidence"]) == 0 and Decimal(output["uncertainty"]) == 1
        assert output["result"]["status"] == "AVAILABLE"
        assert len(output["result"]["basis_instruments"]) == 3
        assert len(output["result"]["dependent_instruments"]) == 1
        scores = {
            row["currency"]: Decimal(row["score_bps"]) for row in output["result"]["estimates"]
        }
        assert scores["EUR"] > 0 > scores["JPY"]
        assert abs(sum(scores.values())) < Decimal("1e-20")
        assert "MACRO_EVIDENCE_UNAVAILABLE" in output["contradictions"]
        assert client.get("/api/v1/agents/graph-runs/strength-1").status_code == 401
    with TestClient(create_app(**arguments)) as client:
        archived = client.get("/api/v1/agents/graph-runs/strength-1", headers=HEADERS)
        assert archived.status_code == 200
        assert archived.json()["receipt"] == receipt


def test_missing_market_yields_escalation_without_invented_currency_values(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["result"]["status"] == "UNAVAILABLE"
        assert all(row["score_bps"] is None for row in output["result"]["estimates"])
        assert output["requires_escalation"] is True


def test_graph_withholds_currency_scores_for_inconsistent_cross_history(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(
            client,
            {
                "EURUSD": [1] * 3,
                "USDJPY": [100] * 3,
                "EURJPY": [100, 101, 100],
                "GBPUSD": ["1.2"] * 3,
            },
        )
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        output = json.loads(receipt["output_json"])
        assert output["result"]["status"] == "INCONSISTENT"
        assert all(row["score_bps"] is None for row in output["result"]["estimates"])
        assert "INCONSISTENT_CROSS_PAIR_RETURNS" in output["contradictions"]


def test_graph_does_not_compare_different_return_windows(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(
            client,
            {"EURUSD": [1, "1.01", "1.02"], "USDJPY": [100, 101, 102]},
            offsets={"USDJPY": 1},
        )
        _, receipt = execute(client)
        assert receipt["status"] == "COMPLETED"
        assert json.loads(receipt["output_json"])["result"]["status"] == "UNALIGNED"


def test_full_64_bar_four_pair_context_fits_declared_budget(tmp_path):
    eur = [Decimal(1) + Decimal("0.0001") * index for index in range(64)]
    yen = [Decimal(100) + Decimal("0.01") * index for index in range(64)]
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(
            client,
            {
                "EURUSD": eur,
                "USDJPY": yen,
                "EURJPY": [base * quote for base, quote in zip(eur, yen)],
                "GBPUSD": [Decimal("1.2")] * 64,
            },
        )
        context, receipt = execute(client)
        assert context["status"] == "READY"
        assert receipt["status"] == "COMPLETED"
        result = json.loads(receipt["output_json"])["result"]
        assert result["status"] == "AVAILABLE" and result["observation_count"] == 64


def test_strength_manifest_retains_original_version_and_fixed_role_ceiling():
    from ai_quant_os.agent_registry import load_agent_catalog

    registry = load_agent_catalog()
    original = registry.get("A04", "0.1.0")
    current = registry.get("A04", "0.2.0")
    assert original.implementation_status == "PLANNED"
    assert current.implementation_status == "PARTIAL"
    assert original.allowed_tools == current.allowed_tools
    assert original.required_inputs == current.required_inputs


def test_uncalibrated_mission_cannot_emit_fabricated_confidence(tmp_path, monkeypatch):
    from ai_quant_os.graph_runtime import AgentImplementation
    from ai_quant_os.currency_strength_agent import currency_strength_implementation

    def install(registry):
        original = currency_strength_implementation(registry)

        async def invented(invocation):
            result = await original.invoke(invocation)
            return result.model_copy(
                update={"confidence": Decimal("0.5"), "uncertainty": Decimal("0.5")}
            )

        return AgentImplementation(original.manifest_ref, original.output_schema, invented)

    monkeypatch.setattr("ai_quant_os.api.currency_strength_implementation", install)
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        _, receipt = execute(client)
        assert receipt["status"] == "INVALID"
        assert receipt["output_json"] is None


def test_source_validation_does_not_inherit_caller_decimal_precision():
    from decimal import localcontext
    from ai_quant_os.currency_strength_sources import currency_strength_sources
    from ai_quant_os.market import MarketSnapshot
    from ai_quant_os.market_data import Bar
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine

    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    bars = tuple(
        Bar("EURUSD", NOW - timedelta(seconds=2 - i), price, price, price, price, Decimal(1))
        for i, price in enumerate(
            (Decimal("1.123456789"), Decimal("1.234567891"), Decimal("1.345678912"))
        )
    )
    engine.record_bars(bars, now=NOW)
    snapshot = engine.snapshot(
        (MarketSnapshot("EURUSD", bars[-1].close, bars[-1].close, NOW, "fixture", True),), now=NOW
    )
    with localcontext() as context:
        context.prec = 9
        assert set(currency_strength_sources(snapshot, engine.histories())) == {
            "currency_twins",
            "cross_pair_returns",
            "macro_state",
            "regime_state",
        }


def test_altered_bar_payload_is_rejected_against_retained_world_lineage(tmp_path, monkeypatch):
    from ai_quant_os.agent_context import EvidenceRecord, SourceBinding
    from ai_quant_os.currency_strength_sources import currency_strength_sources

    def altered(world, histories):
        sources = currency_strength_sources(world, histories)
        original = sources["cross_pair_returns"]

        async def fetch():
            record = await original.fetch()
            payload = json.loads(record.payload_json)
            bar = payload["histories"][0]["bars"][0]
            for field in ("open", "high", "low", "close"):
                bar[field] = "2"
            return EvidenceRecord(**{**record.model_dump(), "payload_json": json.dumps(payload)})

        sources["cross_pair_returns"] = SourceBinding(original.tool, original.resource_class, fetch)
        return sources

    monkeypatch.setattr("ai_quant_os.api.currency_strength_sources", altered)
    with TestClient(
        create_app(
            operator_token="fixture-strength",
            clock=lambda: NOW,
            paper_db_path=tmp_path / "paper.sqlite3",
        )
    ) as client:
        publish(client, {"EURUSD": [1, "1.01", "1.02"]})
        _, receipt = execute(client)
        assert receipt["status"] == "INVALID" and receipt["output_json"] is None


def test_synthetic_baseline_runs_actual_graph_without_quality_or_promotion_claim():
    import asyncio
    import importlib.util
    from pathlib import Path

    assert importlib.util.find_spec("ai_quant_os.currency_strength_evaluation") is not None
    from ai_quant_os.currency_strength_evaluation import evaluate_currency_strength_fixture

    report = asyncio.run(
        evaluate_currency_strength_fixture(
            Path(__file__).parent / "fixtures/currency_strength_baseline.json"
        )
    )
    assert report["case_count"] == report["matching_cases"] == 6
    assert report["provenance"] == "SYNTHETIC_DEVELOPMENT_BASELINE_NOT_HOLDOUT"
    assert report["promotion_approved"] is False
    assert report["calibration_status"] == "UNAVAILABLE"
    assert len(report["fixture_sha256"]) == 64
    assert len(report["source_sha256"]) == 3
    assert all(row["status"] == "COMPLETED" and row["elapsed_ms"] >= 0 for row in report["results"])
    from jsonschema import Draft202012Validator
    from ai_quant_os.currency_strength_agent import CurrencyScoreVector

    validator = Draft202012Validator(CurrencyScoreVector.model_json_schema())
    for row in report["results"]:
        validator.validate(row["output"])


def test_baseline_cli_records_mismatch_as_failed_gate(tmp_path):
    from pathlib import Path
    import subprocess
    import sys

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/currency_strength_baseline.json").read_text()
    )
    fixture["cases"] = [fixture["cases"][0]]
    fixture["cases"][0]["expected_scores"]["EUR"] = "999"
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps(fixture), encoding="utf-8")
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.currency_strength_evaluation",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    report = json.loads(output.read_text())
    assert report["matching_cases"] == 0 and report["case_count"] == 1
    assert report["promotion_approved"] is False


def test_baseline_cli_never_overwrites_retained_report(tmp_path):
    from pathlib import Path
    import subprocess
    import sys

    output = tmp_path / "retained.json"
    output.write_bytes(b"retained independent bytes\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.currency_strength_evaluation",
            str(Path(__file__).parent / "fixtures/currency_strength_baseline.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert output.read_bytes() == b"retained independent bytes\n"
