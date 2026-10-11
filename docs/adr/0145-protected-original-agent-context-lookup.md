# 0145: Read retained SDK context under original prefix and authorization

Date: 2026-10-11. Development, PAPER only.

Expose one canonical advisory-run UUID lookup through the existing operator
token/session boundary. Check authorization before source access and after capture.
The original state lock and one SQLite BEGIN snapshot capture parent run, verified
binding and audit prefix together. Recheck the same prefix before returning.
Capture UTC read time after source retrieval, so a wait cannot predate newly
committed evidence. Reject evidence still future-dated at that captured time.
Reads cannot append, replace/backfill a binding, flush exports or alter controls.

Publish a typed uncached AVAILABLE/NOT_RECORDED/NOT_FOUND/UNAVAILABLE observation.
Revalidate parent/binding models, exact requested identity, UTC clock, nonfuture
recording and strict head/count. Missing/corrupt/malformed sources infer no SDK
context; unavailable also withholds head/count. Only binding metadata is returned,
never original task/world/evidence/rationale or raw source error. Source prefix is
local evidence, with delivery_verified and independently_attested fixed false.

Rollback reverts this additive route/reader/schema only; retain all original rows
and events. Existing binding schema, SDK/runtime/dependency pins and frontend are
unchanged. Detailed console integration/search, other role/order/fill binding,
collector deployment and independent/full P00–P13 acceptance remain pending.
