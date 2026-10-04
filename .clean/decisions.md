# Decisions

- 2026-10-05: Verify one immutable strategy archive snapshot before fixed-engine handoff. Separate read-only presence inspection from a single load request; uncertain mutations retain inputs without retry or shared-image cleanup. Inspect platform/descriptor/layers/runtime config and keep execution/promotion authority false. See ADR 0071.

- 2026-10-05: Assemble the strategy worker OCI archive from independently pinned opaque worker/base bytes. Read-only hash inspection confers no execution or promotion authority. Preserve the older repair path and strategy closures; require external engine/container admission before candidate execution. See ADR 0070.

- 2026-09-28: Implement the first executable slice in Python with FastAPI and pure domain types. Keep the broker route paper-only until later phase gates.
- 2026-09-28: Require an operator token for local write endpoints. All recovery and live-capital enablement remain absent.
- 2026-09-28: Add a local Mission Control monitoring UI and SQLite paper receipts, control state, and audit. External brokers, agents and distributed workflows remain incomplete.
- 2026-09-28: Reserve a unique intent in SQLite before invoking the paper simulator, within one transaction. This is a local simulator safeguard, not a substitute for a live broker outbox and reconciliation.
- 2026-09-28: Compute worst case EURUSD base units from the filled net position and pending buy/sell quantities. Bind each risk decision to the stored snapshot and recheck its fingerprint and fixed ceiling inside the order transaction. See ADR 0002.
