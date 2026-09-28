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
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest -q
cd apps/web
npm run lint
npm run build
```

## Current boundaries

- The risk kernel owns quote freshness, trust, spread, world-state match, intent expiry, order exposure, portfolio exposure, and safety state checks. In the EURUSD paper route, the fixed ceilings are 1,000 base units per order and 1,500 worst case base units across filled positions and pending orders. The gateway rechecks the exact portfolio snapshot inside the SQLite order transaction.
- The paper execution gateway accepts only decisions issued by its risk kernel, allows one order per intent, commits a reservation before calling the simulator, retains accepted simulator acknowledgement IDs, and deduplicates retries. Rejected orders remain as receipts and release reserved exposure. An exception during broker placement creates an `UNKNOWN` outcome, holds exposure, and escalates `NO_NEW_RISK`; restart reads that state from the order receipt. Set `AI_QUANT_PAPER_DB` to retain paper order receipts, manually reported simulated fills, safety state, control receipts, and audit events across restarts.
- The API requires an operator token for all commands and never exposes a broker route other than `PAPER`.
- Safety state escalation is one way. Recovery has no endpoint until a separate policy, reauthentication, and reconciliation workflow exists.

The local data module normalizes UTC bars, detects conflicting duplicates, and replays a fixed dataset deterministically. A simple paper backtest applies fees and slippage on the next bar; NautilusTrader integration is still pending. The authenticated `/api/v1/paper/fills` endpoint records **manual simulated fills** for accepted paper orders. The backend `/api/v1/portfolio/snapshot` reports EURUSD base units and USD trading P&L; a missing or stale quote leaves marked values unavailable. Paper `desired_exposure` is interpreted as EURUSD base units for this simulator, and fill fees are in USD. These values are not broker account equity.

Quotes and issued risk decisions remain in memory. Safety state, control receipts, audit events, order receipts, and fills persist when `AI_QUANT_PAPER_DB` is configured. A `PENDING` order means dispatch never began; retry the original intent and idempotency key with a fresh paper quote to recover it. An expired pending order becomes `REJECTED` and releases its reservation. A `DISPATCHING` or `UNKNOWN` order requires reconciliation and cannot be resent automatically. This local outbox does not provide broker acknowledgement lookup or live reconciliation. This is a development slice, not a shadow or restricted-live release. The remaining work and phase gates are listed in [docs/BUILD_STATUS.md](docs/BUILD_STATUS.md).
