# ADR 0110: Journal observation intent before native worker dispatch

Status: accepted for local groundwork; native acceptance and orphan custody pending.

In-process supervision cannot preserve admission across an owner restart. Add a
dedicated observation journal that commits a UUID4 attempt, owner and preassigned
worker identity before the fixed worker is called. Exact repeated attempts reuse
the saved public receipt; stale quotes are withheld rather than refreshed.

Use transactional SQLite reservations and receipts with the existing audit chain
and projection replay. Any unfinished intent blocks another attempt. Never use a
timeout, owner restart or missing PID as completion evidence. Permit the same live
owner to reconcile only an exact retained child handle with confirmed exit, and
retain that proof until the reconciliation write succeeds. A reconciled lost
observation is withheld, not regenerated.

This is at-most-once observation admission and durable receipt reuse, not exactly
once external execution. The journal does not submit orders, attest terminal
ownership, grant eligibility or replace the future broker order/fill lifecycle.
Independent orphan-process evidence remains necessary after a true owner restart.
Keep the standalone gateway outside API and trading composition until the wider
P04 and release requirements are met.
