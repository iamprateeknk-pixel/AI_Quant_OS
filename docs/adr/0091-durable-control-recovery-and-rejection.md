# 0091: Recover original browser actions and retain terminal rejections

Date: 2026-10-09

## Decision

Checkpoint the exact non-secret original command in origin-scoped browser session
storage before sending it. Validate its bounded fields, UUIDs, UTC timestamp and
typed confirmation. Reject credential/nonce fields. Verify the synchronous write,
refuse replacement of an unresolved command, and refuse sending if retention fails.
Restoration across reloads never sends an action. The operator explicitly checks a
receipt or re-authenticates to retry the identical original command.

Use React's external-store subscription for checkpoint hydration and changes. Clear
the browser record only after validating a terminal receipt with the same action
identity. An invalid or unavailable record disables controls and remains retained.
Browser storage is untrusted metadata, not authorization; backend session, fresh
credentials, expected-state comparison and all safety gates remain authoritative.

Publish an additional terminal REJECTED receipt model for authenticated, well-formed
commands refused due to stale timestamp, stale expected state or forbidden safety
recovery. Retain the complete request fingerprint, observed unchanged state, reason,
policy outcome and receipt trace in one checked SQLite transaction. Recheck the
observed state within the write transaction. Original accepted receipt and command
schemas remain unchanged; the API adds a discriminated terminal outcome union.
Malformed or unauthorized requests still return HTTP errors without action receipts.

## Limits

Session storage survives reloads within a tab, not arbitrary browser/tab closure.
Terminal receipts remain durably available in the backend. There is no backend
PENDING reservation/receipt, new trading authority or recovery shortcut. Denial
auditing/rate limits, session shutdown, realtime recovery, remaining views, governance
and independent acceptance remain open. The full P00–P13 goal remains incomplete.
