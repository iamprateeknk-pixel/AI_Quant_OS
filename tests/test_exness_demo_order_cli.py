import json
from decimal import Decimal
from pathlib import Path

import pytest


def implementation():
    from ai_quant_os import exness_demo_order_cli

    return exness_demo_order_cli


def documents(tmp_path: Path):
    enrollment = {
        "connection": {
            "enabled": True,
            "connection_ack": "CONNECT_DECLARED_DEMO_ONLY",
            "enrollment_id": "6e320dae-472a-4c63-9168-40291982284c",
            "terminal_path": "C:/fixture/terminal64.exe",
            "sdk_version": "5.0.1",
            "expected_login": 610001,
            "expected_server": "fixture-demo-server",
            "expected_company": "Exness fixture",
            "expected_currency": "USD",
            "symbols": [{"instrument_id": "EURUSD", "venue_symbol": "EURUSDm"}],
        },
        "enabled": True,
        "submission_ack": "SUBMIT_EXNESS_DEMO_ONLY",
        "max_order_lots": "0.10",
    }
    command = {
        "order_id": "order-1",
        "idempotency_key": "demo-order-1",
        "risk_id": "risk-1",
        "broker_route": "EXNESS_MT5_DEMO",
        "instrument_id": "EURUSD",
        "order_type": "MARKET",
        "side": "BUY",
        "quantity": "1000",
        "limit_price": None,
        "stop_price": None,
        "time_in_force": "IOC",
        "expires_at": "2027-01-01T00:00:00+00:00",
    }
    enrollment_file = tmp_path / "enrollment.json"
    command_file = tmp_path / "command.json"
    enrollment_file.write_text(json.dumps(enrollment), encoding="utf-8")
    command_file.write_text(json.dumps(command), encoding="utf-8")
    return enrollment_file, command_file, enrollment, command


def test_enrollment_requires_password_from_environment_and_rejects_inline_secrets(tmp_path):
    cli = implementation()
    enrollment_file, _, enrollment_doc, _ = documents(tmp_path)
    with pytest.raises(ValueError, match="environment"):
        cli.load_enrollment(enrollment_file, {})
    enrollment_doc["connection"]["password"] = "do-not-store"
    enrollment_file.write_text(json.dumps(enrollment_doc), encoding="utf-8")
    with pytest.raises(ValueError, match="must come from"):
        cli.load_enrollment(enrollment_file, {"AI_QUANT_MT5_PASSWORD": "fixture-password"})


def test_command_reader_rejects_duplicate_and_extra_fields(tmp_path):
    cli = implementation()
    _, command_file, _, command = documents(tmp_path)
    duplicate = command_file.read_text(encoding="utf-8").replace(
        '"order_id": "order-1",', '"order_id": "order-1", "order_id": "second",'
    )
    command_file.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        cli.load_command(command_file)
    command["market_order"] = True
    command_file.write_text(json.dumps(command), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        cli.load_command(command_file)


def test_cli_requires_both_explicit_operator_confirmation_flags(tmp_path, capsys):
    cli = implementation()
    enrollment_file, command_file, _, _ = documents(tmp_path)
    result = cli.main(
        [
            "--enrollment",
            str(enrollment_file),
            "--command",
            str(command_file),
            "--state-dir",
            str(tmp_path),
        ],
        environment={"AI_QUANT_MT5_PASSWORD": "fixture-password"},
        runner=lambda *args, **kwargs: pytest.fail("Must not execute without submit flag"),
    )
    assert result == 2
    assert "--submit-demo-only" in capsys.readouterr().err


def test_cli_runs_only_the_supervised_demo_gateway_and_redacts_secrets(tmp_path, capsys):
    cli = implementation()
    enrollment_file, command_file, _, _ = documents(tmp_path)
    calls = []

    class Result:
        def model_dump_json(self):
            return '{"state":"RETURNED","report":{"state":"ACKNOWLEDGED"}}'

    def runner(enrollment, command, store_path, *, clock):
        calls.append((enrollment, command, store_path))
        return Result()

    result = cli.main(
        [
            "--enrollment",
            str(enrollment_file),
            "--command",
            str(command_file),
            "--state-dir",
            str(tmp_path),
            "--submit-demo-only",
        ],
        environment={"AI_QUANT_MT5_PASSWORD": "fixture-password"},
        runner=runner,
    )
    assert result == 0
    assert len(calls) == 1
    enrollment, command, store_path = calls[0]
    assert enrollment.connection.password.get_secret_value() == "fixture-password"
    assert command.quantity == Decimal("1000")
    assert store_path.parent == tmp_path.resolve()
    assert "fixture-password" not in capsys.readouterr().out


def test_cli_returns_failure_for_supervisor_result_without_a_report(tmp_path, capsys):
    cli = implementation()
    enrollment_file, command_file, _, _ = documents(tmp_path)

    class Result:
        def model_dump_json(self):
            return '{"state":"WITHHELD","report":null}'

    result = cli.main(
        [
            "--enrollment",
            str(enrollment_file),
            "--command",
            str(command_file),
            "--state-dir",
            str(tmp_path),
            "--submit-demo-only",
        ],
        environment={"AI_QUANT_MT5_PASSWORD": "fixture-password"},
        runner=lambda *args, **kwargs: Result(),
    )
    assert result == 1
    assert '"report":null' in capsys.readouterr().out


def test_cli_reconciliation_requires_its_own_explicit_confirmation_and_uses_read_only_runner(
    tmp_path, capsys
):
    cli = implementation()
    enrollment_file, command_file, _, _ = documents(tmp_path)
    calls = []

    class Result:
        def model_dump_json(self):
            return '{"state":"RETURNED","report":{"state":"UNKNOWN"}}'

    result = cli.main(
        [
            "--enrollment",
            str(enrollment_file),
            "--command",
            str(command_file),
            "--state-dir",
            str(tmp_path),
            "--reconcile-demo-only",
        ],
        environment={"AI_QUANT_MT5_PASSWORD": "fixture-password"},
        runner=lambda *args, **kwargs: pytest.fail("Reconciliation must not submit"),
        reconciliation_runner=lambda *args, **kwargs: calls.append(args) or Result(),
    )
    assert result == 1
    assert len(calls) == 1
    assert '"state":"UNKNOWN"' in capsys.readouterr().out
