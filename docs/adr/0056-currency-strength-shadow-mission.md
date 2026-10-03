# 0056: Execute a checked A04 price decomposition in the paper graph

Date: 2026-10-03

## Decision

Add A04 0.2.0 as a partial shadow mission with the original fixed read ceiling.
Use server-owned, fully lineage-checked histories in the shared persisted graph.
Preserve all original planned manifests and existing A02/A07 implementation bytes.

Use a deterministic currency spanning forest instead of counting every correlated
FX pair as an additional strength vote. Retain excluded/dependent edges and
check cycle residuals at every observation. Withhold inconsistent or asynchronous
comparisons. Center scores only within connected components and disclose missing
data. This is a relative price decomposition, not a calibrated forecast.

Keep macro/calibration unavailable and force zero confidence/escalation. Existing
operator, permission, quarantine, expiry, output, reservation and audit controls
govern execution. Only the A04 source/context byte limits are enlarged to retain
complete bounded histories; source count and mission timeout remain bounded.

## Verification and limits

Tests exercise redundant/inverse edges, contradictory intermediate observations,
unaligned/disconnected/missing evidence, invalid/mutable prices/history, source
tampering, decimal-context independence, complete 64-bar contexts, actual API
graph execution/restart, confidence refusal and baseline CLI mismatch/retention.
A schema failure for scientific-notation decimals was reproduced and corrected
with a fixed-point wire serializer without changing the mathematical values.

The retained six-case synthetic baseline includes exact source/fixture pins and
passes schema/typed validation. Its source pin covers three mission files and the
separate evaluator; it is not a full runtime execution certificate. Promotion,
real macro/calibration/shadow acceptance and A04 repair/genome integration remain
open. See `CURRENCY_STRENGTH_HANDOFF.md` for final verification results.
