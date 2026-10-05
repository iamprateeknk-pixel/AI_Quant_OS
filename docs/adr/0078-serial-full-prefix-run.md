# ADR 0078: Serial full-prefix run with durable reservations

## Status

Implemented complete original-package serial orchestration, recoverable input
release and base/declared-stress native numeric replay. Runtime integration tests
use a scripted daemon; the real fixed Docker Linux endpoint remains unavailable.

## Decision

Pin the original package, complete bar count, candidate, immutable worker/image
handoff, archive inputs, original native root-parent identities and replay quantity/
balance in an operator-created run manifest. The operator independently retains
its canonical manifest SHA-256. Persist append-only audit events and current rows in
SQLite under a separate permanent cooperative root lock. Validate every original
invocation reference against the package before entering the run.

Execute prefixes strictly by increasing source index. Persist an opaque job name and
invocation reservation before staging; inject only that bounded owned basename into
the existing stage allocator. Commit RESERVED, STAGED and exact completed receipt /
recovery archive SHA-256 phases. Before each next reservation, require every previous
prefix to be completed and every reserved root artifact to belong to the run. Keep
the existing image/source admission, one-create/one-start, cleanup and exact release
controls per decision; stop at the first uncertain or invalid outcome.

Commit each exact completed receipt/archive binding to the run audit **before**
releasing the job inputs. A restart can recheck the bundle/current removal record,
finish the exact release and proceed without repeating the worker. Keep local release
archives, released markers, the prior container journal/lock and run audit. Retain
each receipt in the full decision inventory. After full coverage, reserve one native
replay, verify BASE and DECLARED_STRESS with equal pinned quantity/balance, then
publish one hash-bound report. A published-but-unacknowledged report requires its
independent report hash to recover; never overwrite or rerun source work to resolve it.

## Limits

The report is explicitly `UNATTESTED_FULL_PREFIX_RUN_NATIVE_REPLAY`. Existing worker
and execution implementations were not changed: model metadata and scripted daemon
responses are not actual source/SDK execution, container-removal attestation, kernel
confinement or an isolation certificate. The process stops on an ambiguous staged,
create, start or malformed-output phase and leaves custody for explicit per-prefix
reconciliation; it does not guess or automatically retry that decision.

Native simulation preserves the existing synthetic quote/cost and quote-currency
limits. BASE and DECLARED_STRESS results are not independent statistical acceptance,
multiple-testing correction, champion approval or an alpha claim. Financing,
account-currency conversion and independent native realized-P&L checking remain
unavailable. Journal/recovery retention has separate 128 MiB/8 GiB bounds and needs
future retention-governance work. Filesystem/database publication can retain partial
state; malicious host namespaces and directory-entry power-loss durability are not
certified. Production, live-broker and every other blueprint gate remain open.
