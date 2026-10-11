# 0146: Inspect original advisory SDK context from Traces

Date: 2026-10-11. Development, PAPER only.

Add an explicit advisory run UUID lookup on the existing server-rendered Traces
page. It uses a native GET form and keeps the operator session in the existing
private server reader. Validate canonical UUID before cookies or network access;
use one fixed backend path, 4096-byte response bound, three-second deadline,
no-store and redirect rejection. Repeated query values do not trigger a lookup.

Select and validate published response metadata, exact requested UUID, UTC
microsecond ordering, safe event counts, nonzero original SDK context and coherent
missing states. Extra input fields never reach presentation. Display retained
trace/span/runtime identity, original run commitment and observed local audit
prefix separately from current export queue diagnostics. Graph run IDs have a
separate namespace. No mapping or inferred historical link is created.

Sign-in, empty, missing, malformed and unavailable states contain no invented
context. Semantic labels, time elements and existing wrapping provenance styles
are used. Saved context does not certify delivery, complete trace, collector
health, ongoing authorization or independent attestation. No trading mutation,
flush or retry is exposed. Native fixtures validate the original persistence and
SDK context; server rendering/build evidence is not browser acceptance.

Rollback reverts this frontend reader/parser/panel/page addition. Retain all
original bindings and audit events. Collector/span search and full P00–P13 release
acceptance remain open; preserve the prior browser refusal and uncertain custody.
