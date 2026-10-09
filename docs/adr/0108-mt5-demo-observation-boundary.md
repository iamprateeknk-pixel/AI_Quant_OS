# ADR 0108: Separate demo observation policy from terminal execution

Status: accepted for local groundwork; native operation pending.

P04 requires an isolated Exness/MT5 gateway. The existing PAPER router must not
gain terminal initialization or order authority as a side effect of observation.

Use a frozen validated enrollment and a narrowly injected read-only SDK protocol.
Check the exact demo account before and after collecting normalized quotes,
withhold ambiguous facts and cleanup failures, and publish sanitized observations
with explicit denial of trading authority. Keep this module out of API and router
composition until bounded native worker custody exists. Do not import or install
the SDK in this increment.

The facade enables deterministic failure testing but provides no operating system
isolation or broker attestation. Time checks after SDK calls are freshness checks,
not cancellation guarantees. Operational worker supervision and actual demo
acceptance are prerequisites for the next integration step; P04 remains open.
