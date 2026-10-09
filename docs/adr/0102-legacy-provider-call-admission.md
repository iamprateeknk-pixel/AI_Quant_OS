# ADR 0102: Declared ownership for legacy model-call admission

Date: 2026-10-09

## Decision

Wrap both optional legacy model providers at the application boundary using
server-declared cataloged role/version ownership and the existing durable quota.
Require ownership whenever a configured or retained quota exists. Preserve
unconfigured compatibility. Commit logical attempt admission outside the event
loop before dispatch and repeat source/permission checks before accepting results.
Bind full source fingerprints without changing the strict quota JSON encoder.

## Consequences

Accounting ownership does not execute or complete a graph agent mission. Failed,
cancelled or circuit-refused admitted attempts remain charged. Both routes and
versions share the role ceiling with graph and context calls; recovery reserves
remain protected. Invalid ownership fails configuration while unavailable quota
storage withholds provider calls without disabling emergency paper controls.

The public provider status discloses whether call admission is configured and
whether ownership is required. It explicitly withholds token/dollar enforcement
claims. Standalone provider calls, actual token/monetary settlement, complete
cost governance and independent system acceptance remain pending.
