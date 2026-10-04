# ADR 0072: Owned prefix-only request staging

## Status

Implemented for inert input preparation and guarded unstarted discard. Actual
container mounting, execution, ownership recovery and kernel enforcement remain
unverified. The fixed Docker Linux endpoint is unavailable.

## Decision

Reconstruct canonical prefix bytes from the original checked package and index.
Stage exactly one JSON file under a new opaque job name; keep receipt metadata
outside its future mount. Preserve all candidate/SDK text as data on the host.

Bind retained request/parent/job identities and original package references in a
canonical sidecar. Require an independently retained stage hash during restoration.
Use the existing public kernel root ownership protocol and bounded ordinary-file
reader. Refuse changed paths/files or extra job members and keep uncertainty visible.

Provide operator stage/inspect/discard commands. Discard removes only the admitted
request, empty directory and sidecar, and refuses any extra ownership sidecar.
Future create callers must persist ownership before contacting Docker and follow
the same root lock protocol. No metadata file is an execution certificate.

## Consequences

Equal bytes at a substituted inode cannot silently replace the staged request.
Changed parents and copied receipts in a replacement parent are rejected before
opening a lock there. Existing jobs and lock files remain intact under refusal.
The earlier image, worker, wire schemas, strategy closures and research/repair
routes remain unchanged.

Publication/deletion spans separate filesystem operations. Partial failure can
retain an incomplete stage; automatic partial cleanup is not implemented. The host
namespace must be trusted and writers cooperative. These checks are not atomic
host-filesystem fencing against administrators, Windows ACL isolation or Docker
kernel admission. Full container ownership/lifecycle, live mount enforcement,
proposed test jobs and independent source/behavior attestation remain open.
Rollback withdraws these new commands and retains uncertain jobs for inspection.
See `docs/STRATEGY_PREFIX_STAGING_HANDOFF.md` for operator usage and limitations.
