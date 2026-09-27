# Decisions

- 2026-09-28: Implement the first executable slice in Python with FastAPI and pure domain types. Keep the broker route paper-only until later phase gates.
- 2026-09-28: Require an operator token for local write endpoints. All recovery and live-capital enablement remain absent.
- 2026-09-28: Add a local Mission Control monitoring UI and SQLite paper receipts, control state, and audit. External brokers, agents and distributed workflows remain incomplete.
- 2026-09-28: Reserve a unique intent in SQLite before invoking the paper simulator, within one transaction. This is a local simulator safeguard, not a substitute for a live broker outbox and reconciliation.
