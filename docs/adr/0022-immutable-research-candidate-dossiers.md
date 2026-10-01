# ADR 0022: Immutable research candidate dossiers

Status: Accepted for local research inventory; champion governance and promotion remain open.

Derive candidate identity and manifest from a completed verified research archive. Retain its full report and exact implementation source objects as an immutable bundle. Register failed and rejected experiments with their original reasons. Never accept a client-supplied report or approval claim at the API boundary.

Persist registration and challenger withdrawal in a separate SQLite database. Bind every change to an atomic hash-chained event and committed head; use expected revisions and command identity to handle concurrent withdrawal and retries. Keep the original bundle after withdrawal. Preserve previous report serialization and source artifacts for reproducibility.

Expose operator-protected routes and a local CLI. Defer native imports so the default paper API remains usable without native research packages. Keep raw market data in the existing verified archives and disclose their retention requirement.

Research membership is evidence inventory. A challenger has no trading, shadow, champion or promotion authority. Completing remaining gates and implementing comparative champion governance are distinct requirements. Local checks are not an authenticated defense against the host administrator.
