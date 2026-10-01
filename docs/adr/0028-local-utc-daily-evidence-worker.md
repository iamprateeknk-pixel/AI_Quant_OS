# ADR 0028: Run an opt-in local UTC boundary evidence worker

Status: Accepted for local paper reporting.

Daily accounting is implemented, but manual captures do not provide automatic reporting. An application-lifespan worker now captures current UTC boundaries and publishes closed days using only server-owned paper sources. Enabling requires persistence and operator configuration; default operation remains disabled until configured.

Persist activation and consecutive terminal receipts in a hash chain with a committed head. Reserve automatic checkpoint names, preserve the first capture, adopt matching publications after interrupted receipt commits, and record missing boundaries instead of fabricating historical prices. Bound catch-up to seven days per tick and retention to existing archive limits plus 730 receipts. Check audit integrity and clock chronology before new work.

Move accounting/SQLite work off the event loop, drain an in-flight operation at shutdown, and expose protected verified status plus sanitized structured logs. Failed work backs off; missing/late marks remain explicit. Metadata activation alone does not mean the first boundary capture is ready.

Consequences: publication and receipt commits are separate but safely idempotent local writes. Stopped applications miss boundaries; this is not a distributed scheduler or independent timestamp/market attestation. Archive conflicts preserve prior evidence. No capital tools, model calls, agent permissions, notifications or promotion result. Production scheduling evidence, retention/recovery, restatements and comparison/demotion remain open. Rollback disables the worker and preserves the evidence database/WAL and journal; earlier code ignores scheduler tables.
