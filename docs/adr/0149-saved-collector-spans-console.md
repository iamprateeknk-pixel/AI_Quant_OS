# 0149: Inspect saved SDK span observations on Traces

Date: 2026-10-11. Development, PAPER only.

Add SDK trace lookup and selected span table to the existing server-rendered Traces
page. Use an explicit `sdk` query distinct from advisory UUID `run` and graph IDs.
Validate canonical nonzero SDK ID before cookie/network access; fixed protected
backend read, private session, 256 KiB response bound, no-store/redirect rejection
and existing three-second deadline. Repeated query values cause no source read.
Advisory context links only its original SDK ID with prefetch disabled.

Select published metadata and enforce exact requested trace, fixed false authority
flags, source prefix ordinal/size/hash bounds, <=128 spans, nonzero IDs, allowed
SDK operations, safe enums/UUIDs/route templates and coherent missing states.
Validate nanoseconds as decimal strings up to signed 64-bit limit. Use BigInt for
ordering/durations, preserving values beyond JavaScript safe integer range.
Reject duplicate/self/cyclic parentage; preserve and explicitly label absent
parents rather than creating roots. Raw paths/errors/events/extra fields omitted.

Semantic table/labels/time, native GET refresh and source-prefix disclosure reuse
existing wrapping/table-scroll styles. The two lookup forms use scoped theme
tokens, declared 44-pixel input/button targets, visible focus outlines and wrapping
widths; browser measurement remains pending. Empty/disabled/unavailable/sign-in
and loading states infer no outcomes. The scoped Next error component uses installed
16.3.6 `retry()` to refetch/re-render, never logs/renders the raw error. Original
SDK export work and controls remain unaffected. Server rendering/build evidence
is not browser accessibility or visual acceptance.

Frozen fixture is the actual protected response from an original seven-span
native collector file. Its original bytes are retained and an independent API
read at the fixture's recorded clock must equal every returned field. Native
collector integration remains included in scoped backend checks.
Git's default Windows checkout conversion changed this source's byte count/hash;
an explicit binary-text attribute preserves its original native JSONL bytes.

Rollback reverts frontend lookup/parser/panel/page/boundaries and fixture/test
additions while retaining backend files/SDK bindings/audit heads. Saved local
observations do not certify delivery, completeness, atomic audit history or
independent authenticity. Indexing/full search, full role/order/fill causal links,
Linux/Grafana/service/independent/browser acceptance and all P00–P13 remain open.
