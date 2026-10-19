# Local data services

From the repository root, with Python 3.11+ and Docker Compose installed:

```powershell
python scripts/start_data_stack.py
python scripts/check_data_stack.py
```

The first command starts local PostgreSQL, ClickHouse, Valkey, and an S3 API emulator, applies the bar and tick migrations, and creates the `quant-archives` bucket. It creates `.local-stack.env` with random local credentials; the file is ignored by Git. Services bind only to `127.0.0.1`. The second command checks all four services and runs the data integration tests.

`python scripts/start_data_stack.py status` shows container state. `python scripts/start_data_stack.py stop` stops them while preserving PostgreSQL and ClickHouse volumes. Repeating `start` is safe. Preserve `.local-stack.env` with those volumes; startup refuses to generate new credentials if the file is missing while a data volume remains.

The S3 emulator stores objects in memory. Restarting that container clears its bucket and objects, and `start` recreates the empty bucket. Local archive files under `data-local/` are separate and should be retained for replay. The emulator is for development and does not enforce production bucket access policies. `compose.minio.yaml` starts a separate release-pinned PGSTY Silo container, a maintained MinIO-derived S3-compatible server, on `127.0.0.1:58000`. Set the `P02_TEST_S3_*` variables to that endpoint and run `python -m pytest -q tests/test_s3_http.py` to verify real HTTP archive uploads, immutable-object rejection, restoration, and replay. The default local stack does not start this extra service.

The script starts data services, not the paper API or Mission Control web app. The repository [README](../README.md) has their separate startup commands. No service in this stack can route live orders.
