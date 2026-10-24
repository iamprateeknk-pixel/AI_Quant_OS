import base64
from decimal import Context, Decimal, localcontext
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import jsonschema

from fastapi.testclient import TestClient
import pytest

pytest.importorskip("nautilus_trader")

from ai_quant_os.api import create_app
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv
from test_research_hypothesis_api import NOW, HEADERS
from test_validation_package_api import prepare, validation_service_environment
from test_research_hypothesis_api import run as research_run
from test_strategy_builder_api import build
from ai_quant_os.research_hypothesis_contracts import ResearchHypothesis


@pytest.fixture
def package(tmp_path):
    with validation_service_environment(tmp_path) as (settings, holdout, packages):
        with TestClient(create_app(**settings)) as client:
            payload, _, _ = prepare(client, holdout.dataset_ref)
            response = client.post(
                "/api/v1/sandbox/validation/packages", headers=HEADERS, json=payload
            )
            assert response.status_code == 200, response.text
        return packages.get(response.json()["package_ref"])


def tape_payload(package):
    dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64))
    decisions = []
    for i, bar in enumerate(dataset.bars):
        with localcontext(Context(prec=28)):
            history_sha256 = BarDataset(dataset.bars[max(0, i - 2) : i]).fingerprint
        decisions.append(
            {
                "execute_at": bar.observed_at.isoformat(),
                "history_sha256": history_sha256,
                "target": 1 if i == 2 else -1 if i == 3 else 0,
            }
        )
    return {
        "package_ref": package.package_ref,
        "candidate_ref": package.candidate.candidate_ref,
        "dataset_ref": package.holdout.receipt.dataset_ref,
        "dataset_fingerprint": package.holdout.receipt.dataset_fingerprint,
        "decisions": decisions,
    }


def adapter():
    assert importlib.util.find_spec("ai_quant_os.signal_tape_replay") is not None, (
        "Native signal-tape adapter is required"
    )
    from ai_quant_os.signal_tape_replay import replay_signal_tape
    from ai_quant_os.signal_tape_contracts import SignalTape

    return replay_signal_tape, SignalTape


def test_actual_native_tape_reversal_applies_percentage_commissions_and_declared_costs(package):
    replay, model = adapter()
    tape = model.model_validate(tape_payload(package))
    result = replay(package, tape, quantity=10000)
    assert result.engine == "NAUTILUS_BACKTEST" and result.engine_version == "1.221.0"
    assert result.native_order_count == 3
    assert [row.side for row in result.fills] == ["BUY", "SELL", "BUY"]
    assert [row.quantity for row in result.fills] == [
        Decimal(10000),
        Decimal(20000),
        Decimal(10000),
    ]
    assert [row.price for row in result.fills] == [
        Decimal("1.10043"),
        Decimal("1.10007"),
        Decimal("1.10063"),
    ]
    assert [row.fee for row in result.fills] == [Decimal("0.55"), Decimal("1.10"), Decimal("0.55")]
    dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64))
    assert [row.observed_at for row in result.fills] == [
        bar.observed_at for bar in dataset.bars[2:5]
    ]
    assert result.net_pnl == Decimal("-11.40") and result.final_position == 0
    assert result.pnl_currency == "USD" and result.account_currency_basis == "INSTRUMENT_QUOTE_ONLY"
    assert result.source_execution_status == "UNATTESTED"
    assert not result.candidate_code_executed and not result.promotion_authorized
    assert not result.execution_authorized and not result.isolation_verified
    assert result == replay(package, tape, quantity=10000)
    stressed = replay(package, tape, quantity=10000, cost_scenario="DECLARED_STRESS")
    assert stressed.net_pnl == Decimal("-22.40")


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_package",
        "wrong_history",
        "future_clock",
        "missing_decision",
        "invalid_target",
        "warmup_target",
        "authority",
    ],
)
def test_unbound_or_unsafe_tape_is_refused_before_native_success(package, damage):
    replay, model = adapter()
    payload = tape_payload(package)
    if damage == "wrong_package":
        payload["package_ref"] = "validation-package:" + "0" * 64
    elif damage == "wrong_history":
        payload["decisions"][2]["history_sha256"] = "0" * 64
    elif damage == "future_clock":
        payload["decisions"][2]["execute_at"] = NOW.isoformat()
    elif damage == "missing_decision":
        payload["decisions"].pop()
    elif damage == "invalid_target":
        payload["decisions"][2]["target"] = True
    elif damage == "warmup_target":
        payload["decisions"][1]["target"] = 1
    else:
        payload["isolation_verified"] = True
    with pytest.raises(ValueError):
        replay(package, model.model_validate(payload), quantity=10000)


@pytest.mark.parametrize("damage", ["price", "fee", "target", "cost", "mark", "collateral"])
def test_independent_replay_check_refuses_consistent_but_substituted_native_values(package, damage):
    replay, model = adapter()
    result = replay(package, model.model_validate(tape_payload(package)), quantity=10000)
    import ai_quant_os.signal_tape_replay as native

    assert hasattr(native, "verify_signal_replay"), (
        "Replay evidence needs source/cost/target reconciliation"
    )
    body = result.model_dump(mode="json")
    if damage == "price":
        body["fills"][0]["price"] = "1.10044"
        body["net_pnl"] = "-11.50"
    elif damage == "fee":
        body["fills"][0]["fee"] = "0.56"
        body["net_pnl"] = "-11.41"
    elif damage == "target":
        body["tape"]["decisions"][2]["target"] = -1
    elif damage == "cost":
        body["costs"]["commission_bps"] = "0"
    elif damage == "mark":
        body["final_mark"] = "1.2"
    else:
        body["initial_balance"] = "1"
    from ai_quant_os.signal_tape_contracts import SignalReplayReport

    tampered = SignalReplayReport.model_validate(body)
    with pytest.raises(ValueError):
        native.verify_signal_replay(package, tampered)


def test_actual_cli_replays_checked_package_without_overwriting_output(package, tmp_path):
    assert importlib.util.find_spec("ai_quant_os.signal_tape_cli") is not None, (
        "Adapter needs a runnable operator command"
    )
    path = tmp_path / "signals.json"
    path.write_text(json.dumps(tape_payload(package)), encoding="utf-8")
    output = tmp_path / "replay.json"
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.signal_tape_cli",
        "--archive",
        str(tmp_path / "service.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
        "--signals",
        str(path),
        "--quantity",
        "10000",
        "--output",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, timeout=30)
    assert first.returncode == 0, first.stderr.decode()
    result = json.loads(output.read_bytes())
    assert (
        Decimal(result["net_pnl"]) == Decimal("-11.40")
        and result["source_execution_status"] == "UNATTESTED"
    )
    assert not result["candidate_code_executed"] and not result["promotion_authorized"]
    original = output.read_bytes()
    second = subprocess.run(command, capture_output=True, timeout=30)
    assert second.returncode != 0 and output.read_bytes() == original
    assert list(tmp_path.rglob("candidate.py")) == []


@pytest.mark.parametrize("damage", ["oversize", "authority", "missing_archive"])
def test_cli_invalid_input_has_no_success_output_or_new_archive(package, tmp_path, damage):
    assert importlib.util.find_spec("ai_quant_os.signal_tape_cli") is not None
    path = tmp_path / "invalid-signals.json"
    payload = tape_payload(package)
    if damage == "authority":
        payload["isolation_verified"] = True
    path.write_bytes(b"x" * 1048577 if damage == "oversize" else json.dumps(payload).encode())
    archive = tmp_path / (
        "absent.sqlite3" if damage == "missing_archive" else "service.validation-packages.sqlite3"
    )
    output = tmp_path / "must-not-exist.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.signal_tape_cli",
            "--archive",
            str(archive),
            "--package-ref",
            package.package_ref,
            "--signals",
            str(path),
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2 and not result.stdout
    assert not output.exists()
    if damage == "missing_archive":
        assert not archive.exists()


def test_unrepresentable_declared_costs_are_typed_rejection(package):
    replay, model = adapter()
    changed = package.model_copy(
        update={"plan": package.plan.model_copy(update={"spread_bps": Decimal("1e-28")})}
    )
    with pytest.raises(ValueError, match="cost"):
        replay(changed, model.model_validate(tape_payload(changed)), quantity=10000)


def test_unrepresentable_native_fee_rate_cannot_silently_become_zero(package):
    replay, model = adapter()
    changed = package.model_copy(
        update={"plan": package.plan.model_copy(update={"commission_bps": Decimal("1e-1000023")})}
    )
    with pytest.raises(ValueError, match="cost"):
        replay(changed, model.model_validate(tape_payload(changed)), quantity=10000)


def currency_package(root, identifier, plan):
    prices = [
        Decimal(110) + Decimal(i) / 100
        if identifier.endswith("JPY")
        else Decimal("1.1") + Decimal(i) / 10000
        for i in range(12)
    ]
    csv = "instrument_id,observed_at,open,high,low,close,volume\n" + "".join(
        f"{identifier},2026-10-01T00:{i:02}:00Z,{p},{p},{p},{p},1\n" for i, p in enumerate(prices)
    )
    holdout_csv = csv.replace("2026-10-01", "2026-10-02").encode()
    with validation_service_environment(
        root,
        holdout_raw=holdout_csv,
        paper_db_path=root / f"{identifier}.sqlite3",
    ) as (
        settings,
        holdout,
        packages,
    ):
        with TestClient(create_app(**settings)) as client:
            published = client.post(
                "/api/v1/paper/research/datasets",
                headers=HEADERS,
                json={"source_id": "development", "resource_class": "TRAINING", "csv": csv},
            )
            assert published.status_code == 200, published.text
            _, _, output = research_run(client)
            hypothesis = ResearchHypothesis.model_validate(output["hypotheses"][0])
            reviewed = client.post(
                "/api/v1/sandbox/research/reviews",
                headers=HEADERS,
                json={
                    "review_id": "currency-review",
                    "run_id": "research-1",
                    "hypothesis_signature": hypothesis.signature,
                    "rationale": "Synthetic native currency check.",
                },
            )
            assert reviewed.status_code == 200, reviewed.text
            build(client, reviewed.json()["review"])
            retained = client.post(
                "/api/v1/sandbox/validation/packages",
                headers=HEADERS,
                json={
                    "run_id": "build-1",
                    "holdout_ref": holdout.dataset_ref,
                    "plan": plan.model_dump(mode="json"),
                },
            )
            assert retained.status_code == 200, retained.text
        return packages.get(retained.json()["package_ref"])


@pytest.mark.parametrize(
    "identifier,currency",
    [("EURUSD", "USD"), ("GBPUSD", "USD"), ("USDJPY", "JPY"), ("EURJPY", "JPY")],
)
def test_native_replay_uses_each_instruments_quote_currency_without_guessing_usd_conversion(
    package, tmp_path, identifier, currency
):
    replay, model = adapter()
    selected = currency_package(tmp_path, identifier, package.plan)
    result = replay(
        selected,
        model.model_validate(tape_payload(selected)),
        quantity=1000,
        initial_balance=Decimal(1000000),
    )
    assert result.instrument_id == identifier and result.pnl_currency == currency
    assert result.account_currency_conversion == "UNAVAILABLE" and result.final_position == 0
    if currency == "JPY":
        assert [row.price for row in result.fills] == [
            Decimal("110.043"),
            Decimal("110.007"),
            Decimal("110.063"),
        ]
        assert [row.fee for row in result.fills] == [Decimal(6), Decimal(11), Decimal(6)]
        assert result.net_pnl == Decimal(-115)


def test_native_denial_does_not_return_partial_tape_success(package):
    replay, model = adapter()
    with pytest.raises(ValueError, match="denied"):
        replay(package, model.model_validate(tape_payload(package)), quantity=500000)


def test_retained_wire_schemas_validate_actual_native_report_and_refuse_false_attestation(package):
    replay, model = adapter()
    tape = model.model_validate(tape_payload(package))
    result = replay(package, tape, quantity=10000)
    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    jsonschema.validate(
        tape.model_dump(mode="json"), json.loads((root / "signal-tape.schema.json").read_bytes())
    )
    schema = json.loads((root / "signal-tape-replay.schema.json").read_bytes())
    jsonschema.validate(result.model_dump(mode="json"), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**result.model_dump(mode="json"), "source_execution_status": "VERIFIED"}, schema
        )
