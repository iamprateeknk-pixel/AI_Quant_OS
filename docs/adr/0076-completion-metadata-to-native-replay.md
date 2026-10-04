# ADR 0076: Complete pinned receipt inventory to native numeric replay

## Status

Implemented metadata collection and actual native numeric replay. Completion
fixtures are synthetic; candidate execution and daemon attestation remain unverified.

## Decision

Require one successful, removed prefix receipt for every original package decision
after its two warmup bars. Reconstruct original invocation identities through the
existing package/source/SDK/history-bound target assembler. Reject missing, extra,
foreign or duplicate decisions, reused owner/job/container custody, independently
mismatched image/source pins and substituted tape targets. Sort receipts by their
invocation reference for a canonical collection identity; preserve chronological
signal decisions. Support the full existing package bound of 4,096 bars.

Replay only the resulting numeric tape through the existing pinned Nautilus adapter
and independently verify its synthetic pricing, fees, fills and cash ledger against
the original package. Retain the collection in the report and verify its exact tape
binding on restoration. Expose explicit independently byte-pinned `assemble` and
`replay` commands with exclusive, fsynced output publication and two retained schemas.

Accept absolute Windows or POSIX job paths as portable metadata, with normalized
path comparison for reused custody. Runtime journal/custody continue to bind actual
native paths to the original local root and staged job. Metadata path acceptance
does not authorize filesystem operations on another host or root.

## Limits

An operator-pinned receipt collection is not proof that a daemon ran the candidate,
enforced its kernel profile or returned truthful output. It does not independently
restore create plans or journal history; those require the prior custody controls.
All execution and promotion authority stays false. The nested replay's
`candidate_code_executed=false` describes numeric replay, not prior worker execution.
No proposed source, SDK or tests execute on the host. Synthetic quotes/costs and
quote-currency accounting retain the old adapter's limits, including no financing,
account-currency conversion, independent native realized-P&L validation or alpha claim.

Collection/replay CLI input and output are bounded to 128 MiB, with at most 4,094
receipts. Publication may retain a partial output after an I/O failure and does not
certify directory-entry power-loss durability. Independent source/behavior
attestation, actual kernel execution, separate proposed compilation/tests, full
prefix orchestration/custody release, statistical acceptance, champion assignment,
promotion and all production gates remain open.
