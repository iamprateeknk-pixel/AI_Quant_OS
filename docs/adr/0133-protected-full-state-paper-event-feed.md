# ADR 0133: Use bounded full-state events for current paper operations

## Decision

Add a protected snapshot and SSE channel using existing operator authorization,
paper safety state and world-state calculation. Use a boot epoch and increasing
safe-integer sequence. Send full state on every event, with explicit reset at
connection start; avoid claiming delta replay or durable global sequencing.

Retain original source validity in a maximum three-second frame lease. Recheck
authority before and after capture. Bound active channels, frames, payloads and
connection lifetime. Keep source errors fail-closed while preserving deterministic
controls and the paper execution path.

## Web boundary

The route mediates an existing HttpOnly session to one fixed loopback stream.
Validate each event and publish selected fields, preserving original times and
sequence identity. Cancel rejected/aborted readers. The client refuses expired,
regressed or unsupported frames and labels retained observations as historical.
Feed state does not replace command acknowledgement. Initial static summaries
remain explicitly identified as page-load observations.

## Limits

Use locked SQL order inventory count instead of materializing all orders/fills
per sample. This inventory read does not certify financial records. Independent
critical review, browser acceptance, production load/distributed consistency,
all-view integration and OTel remain open. No live-capital or provider authority
is added. See REALTIME_PAPER_FEED_HANDOFF.md and evaluation 0133.
