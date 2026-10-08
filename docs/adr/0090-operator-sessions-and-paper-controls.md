# 0090: Backend-owned operator sessions and state-checked paper controls

Date: 2026-10-09

## Decision

Add fifteen-minute backend-owned grants with opaque nonce hashes, credential epoch
binding, durable revocation, bounded capacity and checked audit/projection replay.
Keep the original backend credential independent from session-journal availability
for emergency paper controls. Preserve paper audit/health admission. Require fresh
credential presentation for session safety mutations and check expiry again after
the control lock is acquired.

Add an explicit command/accepted-receipt API. Bind retries to the full original
command, compare expected safety state inside the existing SQLite write
transaction, and hash the durable receipt into the existing paper audit. Keep
legacy receipt contracts intact, including arbitrary original idempotency keys.
There is no recovery shortcut or capital routing authority.

Mission Control stores the backend nonce in an HttpOnly SameSite Strict host-only
cookie and sends credentials only through its server boundary to the literal local
API origin. Mutations require an exact configured Origin and Host. The UI clears
password inputs, waits for receipts, preserves the same action UUID for retries,
offers receipt lookup after uncertain acknowledgements, and blocks submission
while authoritative safety state is refreshing.

## Limits

This is a local P12 increment, not completion of P12 or the full execution pack.
Only accepted decisions have durable receipts. Browser pending state does not
survive reload. Distributed kernels, hosted login, denial audit/rate limits,
realtime recovery, complete view implementations, governance and independent
release acceptance remain open. See `OPERATOR_CONTROL_HANDOFF.md`.
