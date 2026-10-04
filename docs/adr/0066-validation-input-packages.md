# 0066: Retain inert validation inputs before admitting proposed execution

Date: 2026-10-04
Status: Accepted

The A09 builder retains proposed source/tests and observes two SDK hashes. The
next validation stage needs reproducible input bytes. An input package now binds
its original checked graph/candidate to exact training/later holdout receipts and
CSV, four observed static SDK files, and an immutable operator-declared plan.
Candidate source/tests stay inert; no executable export, worker, scorecard or A10
mission is admitted by this change.

Use a separate bounded SQLite archive with atomic audit binding. Identical
requests preserve the original timestamp; plan IDs cannot be rewritten. Serve
only metadata over operator-protected HTTP. Trusted preparation has a checked
raw-pair catalog method, which is never exposed to a generation agent. Extract
the existing strict CSV parser unchanged for shared receipt/snapshot verification.
Keep original pinned A08/A09 sources and receipts unchanged.

Observed static SDK bytes do not establish runtime closure, independent
attestation or kernel confinement. The operator-selected local holdout does not
establish blindness or statistical independence. Costs are explicitly synthetic;
financing/conversion and the acceptance protocol remain incomplete. Validation,
test execution, promotion and all live authority remain disabled.

See VALIDATION_PACKAGE_HANDOFF.md and BUILD_STATUS.md for usage and evidence.
