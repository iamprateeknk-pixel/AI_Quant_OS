"""Start and bootstrap the disposable local data-service stack."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".local-stack.env"
COMPOSE_FILE = ROOT / "compose.data.yaml"
CREDENTIAL_KEYS = (
    "AI_QUANT_LOCAL_POSTGRES_PASSWORD",
    "AI_QUANT_LOCAL_CLICKHOUSE_PASSWORD",
    "AI_QUANT_LOCAL_VALKEY_PASSWORD",
    "AI_QUANT_LOCAL_S3_ACCESS_KEY",
    "AI_QUANT_LOCAL_S3_SECRET_KEY",
)


def validate_environment() -> None:
    try:
        values = dict(
            line.split("=", 1) for line in ENV_FILE.read_text(encoding="utf-8").splitlines()
        )
    except ValueError as error:
        raise RuntimeError("Local stack credential file is incomplete") from error
    if not all(values.get(name) for name in CREDENTIAL_KEYS):
        raise RuntimeError("Local stack credential file is incomplete")


def ensure_environment() -> None:
    if ENV_FILE.exists():
        validate_environment()
        return
    for volume in (
        "ai-quant-data-local_postgres-data",
        "ai-quant-data-local_clickhouse-data",
    ):
        result = subprocess.run(
            ["docker", "volume", "inspect", volume],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            raise RuntimeError(
                "Found existing local data but no credential file. Restore .local-stack.env "
                "before starting the stack."
            )
    values = {
        "AI_QUANT_LOCAL_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "AI_QUANT_LOCAL_CLICKHOUSE_PASSWORD": secrets.token_urlsafe(32),
        "AI_QUANT_LOCAL_VALKEY_PASSWORD": secrets.token_urlsafe(32),
        "AI_QUANT_LOCAL_S3_ACCESS_KEY": secrets.token_urlsafe(16),
        "AI_QUANT_LOCAL_S3_SECRET_KEY": secrets.token_urlsafe(32),
    }
    content = "".join(f"{name}={value}\n" for name, value in values.items())
    try:
        descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        validate_environment()
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def compose(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(COMPOSE_FILE), *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def postgres_table_exists(table: str) -> bool:
    result = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-At",
        "-U",
        "quant_dev",
        "-d",
        "quant_dev",
        "-c",
        f"SELECT to_regclass('quant_data.{table}') IS NOT NULL",
        capture=True,
    )
    return result == "t"


def bootstrap_postgres() -> None:
    for table, migration in (
        ("bar_archives", "0001_lineage.up.sql"),
        ("tick_archives", "0002_tick_lineage.up.sql"),
    ):
        if not postgres_table_exists(table):
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "quant_dev",
                "-d",
                "quant_dev",
                "-f",
                f"/migrations/{migration}",
            )


def bootstrap_clickhouse() -> None:
    for migration in ("0001_market_bars.sql", "0002_market_ticks.sql"):
        script = (
            'clickhouse-client --user "$CLICKHOUSE_USER" --password '
            '"$CLICKHOUSE_PASSWORD" --multiquery < "/migrations/' + migration + '"'
        )
        compose("exec", "-T", "clickhouse", "sh", "-ec", script)


def bootstrap_s3() -> None:
    script = """import boto3
from botocore.exceptions import ClientError
client = boto3.client('s3', endpoint_url='http://127.0.0.1:5000', region_name='us-east-1')
try:
    client.head_bucket(Bucket='quant-archives')
except ClientError as error:
    if error.response['ResponseMetadata']['HTTPStatusCode'] != 404:
        raise
    client.create_bucket(Bucket='quant-archives')
"""
    compose("exec", "-T", "s3-emulator", "python", "-c", script)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI Quant OS data services")
    parser.add_argument("action", choices=("start", "stop", "status"), nargs="?", default="start")
    args = parser.parse_args()
    if args.action == "start":
        ensure_environment()
        compose("up", "-d", "--wait", "--wait-timeout", "180")
        bootstrap_postgres()
        bootstrap_clickhouse()
        bootstrap_s3()
        print(
            "Local data services ready on 127.0.0.1 (PostgreSQL 55432, ClickHouse 58123, Valkey 56379, S3 59000)."
        )
        print(f"Local credentials are in {ENV_FILE}; keep this file private.")
    elif args.action == "stop":
        if not ENV_FILE.exists():
            parser.error("Local stack credential file is missing")
        compose("down")
    else:
        if not ENV_FILE.exists():
            parser.error("Local stack credential file is missing")
        compose("ps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
