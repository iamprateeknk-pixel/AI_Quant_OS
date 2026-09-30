# AI Quant OS

This repository starts the execution pack as a **local, paper-only** trading foundation. It accepts simulated quotes and trade intents, checks deterministic risk rules, records paper order receipts, and exposes a local control API. It does not connect to Exness, MT5, any broker, or live capital.

## Run locally

Use Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
$env:AI_QUANT_OPERATOR_TOKEN = "<generate-a-random-local-token>"
$env:AI_QUANT_PAPER_DB = ".\paper.sqlite3"
.\.venv\Scripts\python -m uvicorn ai_quant_os.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for the API. Without `AI_QUANT_OPERATOR_TOKEN`, all write commands return 503. Read endpoints show the paper mode, risk state, orders, and audit events. Do not expose this development server beyond localhost.

The Mission Control frontend is in `apps/web`. In a second terminal:

```powershell
cd apps/web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. The frontend reads `AI_QUANT_API_URL` on the server and defaults to `http://127.0.0.1:8000`. It displays unavailable states for services not connected yet. It does not expose control commands until operator-session authentication is built.

## Checks

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m pytest -q
cd apps/web
npm run lint
npm run build
```

The optional local data stack starts PostgreSQL, ClickHouse, Valkey, and an S3 emulator with `python scripts/start_data_stack.py`. Run `python scripts/check_data_stack.py` to verify the services and integration tests. See [data/LOCAL_STACK.md](data/LOCAL_STACK.md).

## Current boundaries

- The risk kernel owns quote freshness, trust, spread, world-state match, intent expiry, order exposure, portfolio exposure, and safety state checks. For each of the EURUSD, GBPUSD, USDJPY and EURJPY paper routes, the fixed ceilings are 1,000 base units per order and 1,500 worst case base units across its filled position and pending orders. Unit quantities are kept separate by instrument. The gateway rechecks the exact portfolio snapshot inside the SQLite order transaction. The API also enforces a fixed USD-valued ceiling of 2,000 per currency and 4,000 gross across both currency legs. Currency prices and ledger state are checked again at reservation and before dispatch.
- The paper execution gateway accepts only decisions issued by its risk kernel, allows one order per intent, commits a reservation before calling the simulator, retains accepted simulator acknowledgement IDs, and deduplicates retries. Rejected orders remain as receipts and release reserved exposure. An exception during broker placement creates an `UNKNOWN` outcome, holds exposure, and escalates `NO_NEW_RISK`; restart reads that state from the order receipt. Set `AI_QUANT_PAPER_DB` to retain paper order receipts, manually reported simulated fills, safety state, control receipts, and audit events across restarts.
- The API requires an operator token for all commands and never exposes a broker route other than `PAPER`.
- `NO_NEW_RISK` paper recovery requires a separately configured `AI_QUANT_RECOVERY_TOKEN`, both token headers, an idempotency key and reason, no unresolved paper orders, and matching local and simulated broker fill ledgers. `FLATTEN_ONLY` and `HALT` cannot be lowered through this API.

The local data module normalizes UTC bars, detects conflicting duplicates, and replays a fixed dataset deterministically. A simple paper backtest applies fees and slippage on the next bar; NautilusTrader integration is still pending. The authenticated `/api/v1/paper/fills` endpoint records **manual simulated fills** directly in the local portfolio. `/api/v1/paper/broker-fills` records a fill in the durable paper simulator, and `/api/v1/paper/orders/{order_id}/sync-fills` imports its fills into the portfolio atomically. `/api/v1/paper/positions/reconcile` compares local and simulator fill IDs, net base units, and quote-currency cash flow separately for every instrument. Its version 1.1 response contains an instruments list; legacy scalar fields explicitly summarize EURUSD. A mismatch persists `NO_NEW_RISK` and requires reconciliation. The backend `/api/v1/portfolio/snapshot` defaults to EURUSD and accepts an `instrument` query parameter; `/api/v1/portfolio/positions` reports all configured positions with their base and quote currencies; a missing or stale quote leaves marked values unavailable. Paper `desired_exposure` is interpreted as the selected instrument's base units, and fill fees are in its quote currency. `/api/v1/instruments` exposes the fixed paper catalog. `/api/v1/risk?instrument=GBPUSD` selects the corresponding unit ledger. These values are not broker account equity.

For historical bars, install `.[data]` and use `ai-quant-data ingest <csv> --root ./data-local --source-id <source>`. The archive preserves exact CSV bytes, writes a typed Parquet copy, records lineage and duplicate counts, and verifies checksums on replay. `ai-quant-data health <archive-id> --root ./data-local --max-age-seconds 300` reports freshness. See [data/HANDOFF.md](data/HANDOFF.md) for the object layout and migration status.

`GET /api/v1/portfolio/currencies` reports the filled currency balances and the possible balances after pending paper orders. It includes quote-currency fees and each pending intent's slippage allowance, and uses fresh bid/ask conversions to USD. Missing or stale prices leave aggregate values unavailable. Mission Control shows these bounds on the portfolio page. The underlying engine supports explicit metadata for multiple instruments and direct or inverse conversion pairs; the API supports four simulated pairs, with shared currency limits enforced during approval, reservation and pending dispatch. See [the currency valuation decision](docs/adr/0003-currency-ledger-valuation.md).

Quotes and issued risk decisions remain in memory. Safety state, control and recovery receipts, audit events, order receipts, simulated broker acknowledgements, and fills persist when `AI_QUANT_PAPER_DB` is configured. A `PENDING` order means dispatch never began; retry the original intent and idempotency key with a fresh paper quote to recover it. An expired pending order becomes `REJECTED` and releases its reservation. A `DISPATCHING` or `UNKNOWN` order cannot be resent automatically. An authenticated `POST /api/v1/paper/orders/{order_id}/reconcile` checks the durable simulator acknowledgement and resolves a known outcome without dispatch; a missing acknowledgement leaves exposure reserved. Uncertain order escalation is persisted and reconciliation does not automatically lower `NO_NEW_RISK`. After resolving all uncertain orders and fill mismatches, the operator can POST `/api/v1/control/recover-paper` with `X-Operator-Token`, `X-Recovery-Token`, `Idempotency-Key`, and a JSON `reason`. This paper recovery is disabled unless the second token is configured and distinct. Broker sourced fills and live reconciliation remain unimplemented. This is a development slice, not a shadow or restricted-live release. The remaining work and phase gates are listed in [docs/BUILD_STATUS.md](docs/BUILD_STATUS.md).
