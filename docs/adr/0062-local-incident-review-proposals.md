# 0062: Local incident classification and immutable review proposals

Date: 2026-10-03

## Decision

Install A18 0.2.0 as a partial shadow mission using its original role ceiling.
Read complete checked paper health/order/fill collections and current world/risk
facts; summarize every retained incident code with collection identities. Produce
a deterministic source-bound recommendation and an immutable local operator review
proposal. Keep safety transitions, recovery and actual workflow dispatch outside
the mission's authority.

Use runtime-bound task/context identity and an empty tool payload. Recompute
proposal content from the retained context. Check current expiry, permission and
safety state before and after the awaited archive write. Join owned publication
on cancellation; retain committed records as archival without issuing a current
recommendation when the final checks fail. Persist proposals atomically with audit
events in a separate bounded SQLite archive and refuse damaged metadata or rows.

## Evidence and limits

Expiry during publication was reproduced as a failing test before the final gate
was added. Malformed pure-policy facts were also reproduced before validation.
Four synthetic API replays cover missing evidence, stale quote failure, critical
position mismatch and future-dated health evidence, including exact restart
receipts and unchanged controls. Full regression evidence is in BUILD_STATUS.

Source capture is not atomic across stores. Collection hashes rely on the trusted
source publisher and lack independent external anchoring. Paper simulator flags
provide no actual live broker health. Confidence and uncertainty remain 0 and 1;
no calibration, automatic recovery, alert delivery or promotion is approved.
A01's existing named source policy remains unchanged and defers this new mission.

## Rollback

Preserve all application and new proposal state before reverting. Older runtime
versions may not serve A18 receipts. Archival records are not permission to resume
or dispatch their recommended workflow. See INCIDENT_SUPERVISOR_HANDOFF.
