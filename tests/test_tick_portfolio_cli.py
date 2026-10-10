import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from test_native_tick_portfolio import inputs


def plan_files(tmp_path):
    legs = []
    hashes = []
    for leg in inputs():
        buffer = io.StringIO(newline="")
        fields = (
            "instrument_id",
            "source_id",
            "sequence",
            "observed_at",
            "bid",
            "ask",
            "bid_size",
            "ask_size",
        )
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for tick in leg.dataset.ticks:
            row = {field: getattr(tick, field) for field in fields}
            row["observed_at"] = tick.observed_at.isoformat()
            writer.writerow(row)
        raw = buffer.getvalue().encode("utf-8")
        path = tmp_path / (leg.dataset.ticks[0].instrument_id + ".csv")
        path.write_bytes(raw)
        hashes.append(sha256(raw).hexdigest())
        legs.append(
            {
                "csv": path.name,
                "genome": {
                    "strategy_ref": "tick-momentum@1.0.0",
                    "input_kind": "TICK",
                    "parameters": [],
                },
                "quantity": 10,
            }
        )
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"schema_version": "1.0", "legs": legs, "fee_per_order_usd": ".10"}),
        encoding="utf-8",
    )
    return path, hashes


def command(plan, output):
    assert importlib.util.find_spec("ai_quant_os.tick_portfolio_cli") is not None
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.tick_portfolio_cli",
            str(plan),
            "--output",
            str(output),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_actual_cli_exports_source_bound_native_report_immutably(tmp_path):
    plan, hashes = plan_files(tmp_path)
    output = tmp_path / "report.json"
    result = command(plan, output)
    assert result.returncode == 0, result.stderr
    raw = output.read_bytes()
    record = json.loads(raw)
    assert record["report"]["net_pnl_usd"] == "-10.6050"
    assert record["report"]["promotion_allowed"] is False
    import jsonschema

    schemas = Path(__file__).resolve().parents[1] / "docs" / "schemas"
    jsonschema.validate(
        record["report"],
        json.loads(
            (schemas / "native-tick-portfolio-report.schema.json").read_text(encoding="utf-8")
        ),
    )
    jsonschema.validate(
        json.loads(plan.read_text(encoding="utf-8")),
        json.loads(
            (schemas / "native-tick-portfolio-plan.schema.json").read_text(encoding="utf-8")
        ),
    )
    assert [row["source_sha256"] for row in record["source_lineage"]] == hashes
    assert command(plan, output).returncode == 0 and output.read_bytes() == raw
    output.write_bytes(b"preserved")
    assert command(plan, output).returncode != 0 and output.read_bytes() == b"preserved"


def test_cli_rejects_duplicate_json_keys_and_input_overwrite(tmp_path):
    plan, _ = plan_files(tmp_path)
    raw = plan.read_text(encoding="utf-8")
    plan.write_text(
        raw.replace('"schema_version": "1.0"', '"schema_version": "bad", "schema_version": "1.0"'),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    assert command(plan, output).returncode != 0 and not output.exists()
    plan.write_text(raw, encoding="utf-8")
    assert command(plan, plan).returncode != 0 and plan.read_text(encoding="utf-8") == raw
