# ADR 0099: Protected agent call capacity observations

Date: 2026-10-09

## Decision

Connect the existing Costs section to the durable routine call reservations from
ADR 0098 through the existing operator session server transport. Render only
validated public observations. Require all 18 unique roles, coherent periods,
bounded counters, original policy/journal fingerprints and false recovery/token/
monetary accounting claims.

Expose daily/monthly attempts, routine capacity and protected recovery reserves.
Keep disabled, unavailable and sign-in states distinct. No write or reset controls
are added. Provider/compute/data charges remain unmeasured.

## Consequences

Operators can inspect enforced capacity without exposing credentials, request
contents or treating failed attempts as refunded. The observation can become stale
after rendering; explicit refresh obtains another verified snapshot. Responsive,
keyboard and browser privacy acceptance remain pending after the policy-rejected
preview launch. Full cost governance and P00-P13 release gates remain open.
