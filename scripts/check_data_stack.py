"""Verify local data services and run their integration tests."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import boto3
import psycopg
import redis


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".local-stack.env"


def read_credentials() -> dict[str, str]:
    if not ENV_FILE.exists():
        raise RuntimeError("Start the local data stack before running integration checks")
    values = dict(line.split("=", 1) for line in ENV_FILE.read_text(encoding="utf-8").splitlines())
    required = {
        "AI_QUANT_LOCAL_POSTGRES_PASSWORD",
        "AI_QUANT_LOCAL_CLICKHOUSE_PASSWORD",
        "AI_QUANT_LOCAL_VALKEY_PASSWORD",
        "AI_QUANT_LOCAL_S3_ACCESS_KEY",
        "AI_QUANT_LOCAL_S3_SECRET_KEY",
    }
    if not all(values.get(name) for name in required):
        raise ValueError("Local data stack credential file is incomplete")
    return values


def verify_services(values: dict[str, str]) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=55432,
        user="quant_dev",
        password=values["AI_QUANT_LOCAL_POSTGRES_PASSWORD"],
        dbname="quant_dev",
        connect_timeout=3,
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'quant_data'"
            )
        }
    assert tables == {
        "sources",
        "bar_archives",
        "tick_archives",
        "quality_observations",
        "tick_quality_observations",
    }, "PostgreSQL data schema is incomplete"

    request = Request(
        "http://127.0.0.1:58123/?query=SELECT%20name%20FROM%20system.tables%20"
        "WHERE%20database%3D%27quant_market%27%20ORDER%20BY%20name",
        headers={
            "X-ClickHouse-User": "quant_dev",
            "X-ClickHouse-Key": values["AI_QUANT_LOCAL_CLICKHOUSE_PASSWORD"],
        },
    )
    with urlopen(request, timeout=5) as response:
        tables = response.read().decode().split()
    assert tables == ["bars_v1", "ticks_v1"], "ClickHouse data schema is incomplete"

    cache = redis.Redis(
        host="127.0.0.1",
        port=56379,
        password=values["AI_QUANT_LOCAL_VALKEY_PASSWORD"],
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    assert cache.ping(), "Valkey is unavailable"

    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:59000",
        region_name="us-east-1",
        aws_access_key_id=values["AI_QUANT_LOCAL_S3_ACCESS_KEY"],
        aws_secret_access_key=values["AI_QUANT_LOCAL_S3_SECRET_KEY"],
    )
    client.head_bucket(Bucket="quant-archives")


def test_environment(values: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        P02_TEST_DATABASE_URL=(
            "postgresql://quant_dev:"
            + quote(values["AI_QUANT_LOCAL_POSTGRES_PASSWORD"], safe="")
            + "@127.0.0.1:55432/quant_dev"
        ),
        P02_TEST_VALKEY_URL=(
            "redis://:"
            + quote(values["AI_QUANT_LOCAL_VALKEY_PASSWORD"], safe="")
            + "@127.0.0.1:56379/0"
        ),
        P02_TEST_S3_ENDPOINT="http://127.0.0.1:59000",
        P02_TEST_S3_ACCESS_KEY=values["AI_QUANT_LOCAL_S3_ACCESS_KEY"],
        P02_TEST_S3_SECRET_KEY=values["AI_QUANT_LOCAL_S3_SECRET_KEY"],
        P02_TEST_S3_BUCKET="quant-archives",
    )
    return environment


def main() -> int:
    values = read_credentials()
    verify_services(values)
    print("PostgreSQL, ClickHouse, Valkey, and S3 emulator are ready")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_lineage_catalog.py",
            "tests/test_valkey_cache.py",
            "tests/test_s3_http.py",
        ],
        cwd=ROOT,
        env=test_environment(values),
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
