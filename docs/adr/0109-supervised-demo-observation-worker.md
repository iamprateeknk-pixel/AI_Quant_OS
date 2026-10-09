# ADR 0109: Supervise demo observations through a fixed worker protocol

Status: accepted for local groundwork; native and gateway acceptance pending.

The MT5 SDK can block and may launch a terminal. The observation policy's
post-call timestamp checks cannot provide cancellation. Execute that policy
behind one trusted Python child with bounded stdin/stdout/stderr and a monotonic
budget. Validate the returned facts against the enrollment and current expiry.
Suppress native diagnostic output before loading the SDK; publish fixed failure
codes only. Never submit orders or grant portfolio authority from this protocol.

Own and terminate only the exact Python child handle. Retain uncertain handles
in memory and deny overlapping requests until exit and pipe completion can be
observed. Do not infer terminal ownership, kill shared terminals or retry unknown
outcomes. Durable custody and restart recovery are a separate required gateway
step, not established by this standalone supervisor.

The fixed worker accepts only declared demo quotes, remains absent from the API
and trading router, and requires a separately managed trusted native runtime.
Fixture transport checks prove protocol and local supervision behavior; they do
not certify the native broker environment or meet P04/P13 release acceptance.
