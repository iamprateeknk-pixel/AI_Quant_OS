import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_quant_os.api import create_app

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
HEADERS = {"x-operator-token": "fixture"}


def assess(client):
    context = client.post(
        "/api/v1/paper/agents/context",
        headers=HEADERS,
        json={
            "agent_id": "A07",
            "agent_version": "0.2.0",
            "task_id": "health",
        },
    )
    assert context.status_code == 200, context.text
    result = client.post(
        "/api/v1/paper/agents/execute",
        headers=HEADERS,
        json={
            "run_id": "integrity-1",
            "context_ref": context.json()["context_ref"],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["receipt"]["status"] == "COMPLETED"
    return json.loads(result.json()["receipt"]["output_json"])


def quote(client, age=0, bid="1.1", ask="1.1001"):
    return client.post(
        "/api/v1/paper/quotes",
        headers=HEADERS,
        json={
            "instrument_id": "EURUSD",
            "bid": bid,
            "ask": ask,
            "observed_at": (NOW - timedelta(seconds=age)).isoformat(),
            "world_state_version": "fixture",
        },
    )


def bars(client, closes=("1.1", "1.1001", "1.1002")):
    return client.post(
        "/api/v1/paper/world-state/bars",
        headers=HEADERS,
        json={
            "bars": [
                {
                    "instrument_id": "EURUSD",
                    "observed_at": (NOW - timedelta(seconds=2 - i)).isoformat(),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": "1",
                }
                for i, close in enumerate(closes)
            ],
        },
    )


def test_integrity_agent_diagnoses_empty_feeds_without_changing_safety(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        output = assess(client)
        assert output["agent_id"] == "A07" and output["agent_version"] == "0.2.0"
        assert output["status"] == "UNUSABLE"
        assert len(output["quarantined_instruments"]) == 4
        assert output["incident_request"]["requested_mode"] == "NO_NEW_RISK"
        assert output["incident_request"]["automatic_control_change"] is False
        assert output["clock_status"] == "UNVERIFIED"
        assert output["requires_escalation"] is True
        assert client.get("/api/v1/system/status").json()["safety_state"] == "NORMAL"


@pytest.mark.parametrize("age, issue", [(6, "QUOTE_STALE"), (-1, "QUOTE_FUTURE")])
def test_bad_quote_is_quarantined_while_health_observation_remains_current(tmp_path, age, issue):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        assert quote(client, age).status_code == 200
        assert bars(client).status_code == 200
        output = assess(client)
        eur = next(row for row in output["instruments"] if row["instrument_id"] == "EURUSD")
        assert eur["usable"] is False
        assert issue in eur["issues"]
        assert "EURUSD" in output["quarantined_instruments"]


def test_rejected_and_duplicated_ingress_is_reported_without_replacement_prices(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        assert quote(client, bid="1.2", ask="1.1").status_code == 422
        assert quote(client).status_code == 200
        assert bars(client).status_code == 200
        assert bars(client).status_code == 200
        assert bars(client, ("1.1", "1.1001", "1.1003")).status_code == 422
        output = assess(client)
        assert output["ingress_counts"]["INVALID_QUOTE"] == 1
        assert output["ingress_counts"]["CONFLICTING_BAR"] == 1
        assert output["ingress_counts"]["DUPLICATE_BAR"] >= 3
        eur = next(row for row in output["instruments"] if row["instrument_id"] == "EURUSD")
        assert eur["usable"] is True
        assert "price" not in eur and "replacement_price" not in output


def test_schema_rejected_ingress_is_reported_without_request_contents(tmp_path):
    with TestClient(
        create_app(
            operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
        )
    ) as client:
        bad_quote = client.post(
            "/api/v1/paper/quotes",
            headers=HEADERS,
            json={"instrument_id": "EURUSD", "bid": "private-invalid-value"},
        )
        bad_bars = client.post(
            "/api/v1/paper/world-state/bars",
            headers=HEADERS,
            json={"bars": [{"instrument_id": "EURUSD", "private": "payload-secret"}]},
        )
        assert bad_quote.status_code == 422
        assert bad_bars.status_code == 422

        output = assess(client)
        assert output["ingress_counts"]["SCHEMA_REJECTED_QUOTE"] == 1
        assert output["ingress_counts"]["SCHEMA_REJECTED_BAR"] == 1
        assert "private-invalid-value" not in json.dumps(output)
        assert "payload-secret" not in json.dumps(output)


def test_integrity_receipt_remains_retrievable_after_restart(tmp_path):
    args = dict(
        operator_token="fixture", clock=lambda: NOW, paper_db_path=tmp_path / "paper.sqlite3"
    )
    with TestClient(create_app(**args)) as client:
        output = assess(client)
    with TestClient(create_app(**args)) as client:
        response = client.get("/api/v1/agents/graph-runs/integrity-1", headers=HEADERS)
        assert json.loads(response.json()["receipt"]["output_json"]) == output


@pytest.mark.parametrize("index", [0, 1])
def test_synthetic_lineage_and_clock_fault_replay_through_actual_graph(tmp_path, index):
    import asyncio
    from pathlib import Path
    from ai_quant_os.agent_context import ContextCompiler, EvidenceRecord, SourceBinding
    from ai_quant_os.agent_registry import AgentToolBoundary, load_agent_catalog
    from ai_quant_os.graph_runtime import GraphRuntime
    from ai_quant_os.ingress_health import IngressHealth
    from ai_quant_os.integrity_agent import integrity_implementation

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "integrity_baseline.json").read_text()
    )
    case = fixture["cases"][index]

    async def run():
        registry = load_agent_catalog()
        common = {"world_state_version": "synthetic-fixture"}
        payloads = {
            "feed_health": {
                **common,
                "feeds": [
                    {"instrument_id": "EURUSD", "quote_status": "FRESH", "history_status": "FRESH"}
                ],
                "ingress_counts": IngressHealth().snapshot(),
                "retention": "PROCESS_LOCAL_SINCE_STARTUP",
            },
            "lineage": {
                **common,
                "verified_instruments": case["verified_instruments"],
                "coverage": "LOCAL_BAR_CONTENT_HASH_ONLY",
            },
            "clock_health": {
                **common,
                "status": case["clock_status"],
                "offset_ms": case["offset_ms"],
            },
        }
        sources = {}
        for name, payload in payloads.items():

            async def fetch(payload=payload):
                return EvidenceRecord(
                    source_id="synthetic-fixture",
                    as_of=NOW,
                    valid_until=NOW + timedelta(seconds=5),
                    trusted=True,
                    payload_json=json.dumps(payload),
                )

            sources[name] = SourceBinding("HEALTH_READ", "GENERAL", fetch)
        context = await ContextCompiler(registry, sources, clock=lambda: NOW).compile(
            "A07", "0.2.0", task_id=case["id"], scope="PAPER"
        )
        async with GraphRuntime.open(
            tmp_path / "graph.sqlite3",
            registry=registry,
            implementations={("A07", "0.2.0"): integrity_implementation(registry)},
            tools=AgentToolBoundary(registry, {}),
            clock=lambda: NOW,
        ) as runtime:
            receipt = await runtime.run(case["id"], context)
            assert receipt.status == "COMPLETED"
            output = json.loads(receipt.output_json)
            assert case["expected_issue"] in output["instruments"][0]["issues"]
            assert output["quarantined_instruments"] == ["EURUSD"]

    asyncio.run(run())


def test_duplicate_accounting_is_atomic_under_concurrent_ingestion():
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from ai_quant_os.market_data import Bar
    from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
    from ai_quant_os.world_state import WorldStateEngine

    engine = WorldStateEngine(PAPER_INSTRUMENTS)
    incoming = tuple(
        Bar(
            "EURUSD",
            NOW - timedelta(seconds=i),
            Decimal(1),
            Decimal(1),
            Decimal(1),
            Decimal(1),
            Decimal(1),
        )
        for i in range(3)
    )
    assert hasattr(engine, "record_bars_with_health")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: engine.record_bars_with_health(incoming, now=NOW), range(2))
        )
    assert sum(row[0] for row in results) == 3
    assert sum(row[1] for row in results) == 3
