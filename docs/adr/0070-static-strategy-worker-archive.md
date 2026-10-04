# ADR 0070: Static pinned strategy worker archive

## Status

Local byte assembly and read-only archive integrity verification implemented.
Docker runtime execution and admission remain unverified.

## Decision

Copy the committed standard-library worker and cached pinned Python base layers
into a deterministic OCI archive without running a build engine, source code or
package installer. Keep this path separate from the older repair-worker image
and preserve all earlier source, schema and evaluation pins.

Require independently supplied worker/base pins during assembly and additional
archive/manifest/config pins during inspection. Reconstruct the fixed image config
and inspect exact layer members without extraction. Keep source, execution and
promotion authority false in all receipts. The JSON wire schema is retained at
`docs/schemas/strategy-worker-image.schema.json`; cross-field inventory/state checks
also require the model validator and are not fully expressed by JSON Schema.

## Consequences

Two actual assemblies match byte-for-byte and inspection checks their compressed
and expanded layer hashes. This proves static identity, without demonstrating
Linux enforcement or candidate behavior. Approved pins must come from trusted
review; receipt self-consistency does not create trust. The image has no candidate,
SDK, proposed test or dataset files. Future parent code must independently verify
loaded image/engine/container state, request staging and owned execution cleanup.

The CLI refuses existing output directories. Failed output can remain partial and
must be inspected before choosing a fresh name. Rollback removes use of these new
modules/artifacts; existing strategy, research, repair and paper routes are unchanged.
See `docs/STRATEGY_IMAGE_HANDOFF.md` for commands, exact digests and limitations.
