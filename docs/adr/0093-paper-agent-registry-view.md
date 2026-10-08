# 0093: Display actual paper agent registry versions

Date: 2026-10-09

Read the existing public registry from a Server Component through the shared
uncached API reader. Keep manifest parsing within the agents feature and route
composition within the app layer. Validate and project unknown JSON before display.
Preserve separate registered versions instead of silently selecting a latest
version or reporting a planned role as ready.

Expose development status separately from runtime lifecycle. Tools shown are
declared permission ceilings, not executable capability or health claims. An
unavailable/invalid registry yields no rows; an empty valid registry has its own
message. Native details provide keyboard-accessible contract inspection with no
client execution or action authority.

This is a read-only P12 development increment. Task/health/evaluation histories,
backend-authorized controls and realtime reconciliation remain separate requirements.
