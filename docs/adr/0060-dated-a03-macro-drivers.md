# ADR 0060: A03 dated macro drivers over verified archives

Date: 2026-10-03

## Decision

Connect A03 0.2.0 to checked paper world, archived ECB rates, audited synthetic
event evidence and explicit unavailable release/text/yield facts. Keep dated
rate calculations in a pure module, source verification in the evidence adapter
and the typed envelope in the graph mission. Preserve the original planned
manifest and its read-only capability ceiling.

Distinguish receipt age from rate-change dates and current applicability. Keep
old dated facts descriptive and withhold future receipts/periods from observed
measurements. Represent unassessed currency bias explicitly, with confidence
zero, mandatory escalation and no order/control authority.

Read the independent rate archive beside paper state without initializing it on
absence. Offload verified storage reads and take the world snapshot afterwards.
Refuse unavailable/corrupt storage with 503; never re-baseline source evidence.
Reconstruct world identity/status, verify common source clocks/expiry and retain
the full supported event profile in context.

## Evidence and limits

Retain six declared graph cases and a typed schema, including the original actual
ECB capture and separately disclosed synthetic event/future-period data. Actual
API checks cover restart, archive corruption/locking and unchanged hard quote
rejections. Output validators recompute driver and receipt claims. Full local
verification passed with 1,246 checks and 15 skips; details are recorded in the build status.

Other currencies, yields, releases, central-bank interpretation, point-in-time
vintages, calibration, scheduling, full A03 mission, repair/evolution integration,
production deployment, independent review and every promotion/live gate remain
open. Local archive chains have no independent external anchor.

## Rollback

Preserve the complete state directory and retained bytes with the owned app
stopped before reverting. No shared database migration occurs. Older code may
require a compatible reader for A03 0.2.0 receipts; the planned original version
is not executable.
