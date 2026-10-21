# ADR 0222: Windows HOLDOUT validator identity boundary

Date: 2026-10-20

## Status

Implementation in progress. A Windows service host/installer, strict framed
RPC, service/operator pipe ACL policy, service-token client verification,
recursive data-tree ACL verifier, and A10/A11 service handlers now exist. First
install stages verified SQLite snapshots of the HOLDOUT, validation-package,
candidate-validation, and research archives into an empty data directory before
applying the service ACL. Existing archives are preserved on re-install and
partial archive sets fail closed. A stage and handler test set passes, but
staging refuses a nonempty destination and source validation uses read-only
connections before the temporary snapshots are verified. The focused service
and RPC test set passes 77 tests, but installation has not been run under the
actual Windows service identity.
The PAPER API now requests A10/A11 context snapshots from the service over the
versioned v3 pipe. It does not receive raw HOLDOUT bars or per-fill details when
compiling those contexts. Package preparation and validation result storage
still use the local validation stores, and source archives remain in place, so
the full isolation boundary is not yet established.

## Context

The catalog stores TRAINING and HOLDOUT in separate SQLite files. The PAPER
research API has been changed to open only the TRAINING database, but the
interactive account can still access legacy HOLDOUT files and validation
packages may retain HOLDOUT snapshots. The local workstation's interactive
Windows account is exclusive to the operator, but same-account malware remains
in scope.

An AppContainer launched by the interactive account is insufficient for this
boundary: Windows access is bounded by both the user/group and AppContainer
principals, so granting the AppContainer SID cannot give it file rights that the
interactive account lacks. See Microsoft's [AppContainer launch and access
model](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer),
especially its DACL intersection rule.

## Decision

Run HOLDOUT storage and validation behind a Windows service configured as
`NT AUTHORITY\LocalService` with the service SID
`NT SERVICE\AIQuantHoldout`. Only that service SID and SYSTEM receive a direct
filesystem ACE for the HOLDOUT directory. The
interactive PAPER API identity does not receive a filesystem ACE for that
directory, and it must not open a second path or backup containing the raw
HOLDOUT bytes.

The service owns HOLDOUT registration, sealed metadata, validation packages,
and A10/A11 operations that need HOLDOUT data. The PAPER API calls a bounded
local RPC interface whose ACL permits the authenticated local operator to
submit authorized work. Requests bind candidate, TRAINING and HOLDOUT refs;
responses contain checked status, aggregate summaries, immutable evidence refs,
and hashes only. They do not contain raw bars, per-row predictions, or a store
path. Stress replies may include a bounded list of per-scenario aggregates
(fingerprint, exact PnL, currency, and order count) required to verify A11's
advisory scorecard; they never include individual bars or fills. Service
absence, identity mismatch, ACL mismatch, malformed RPC, or timeout makes
validation unavailable; there is no same-process fallback.

The ordinary PAPER research catalog API keeps TRAINING-only access, and A08
context compilation receives only TRAINING rows. The service may receive the exact
TRAINING snapshot needed for a paired validation request, but does not return it
or HOLDOUT rows to the API. A11 stress evaluation is routed through the same
service before any result is exposed to the graph runtime.

## Required implementation and acceptance

1. Install/upgrade/remove the Windows service idempotently, set its service SID,
   and create/verify the data-directory DACL without granting the interactive
   account access. Preserve data on upgrade and rollback.
2. Implement an ACL-protected, length-bounded local RPC protocol with strict
   schemas, deadlines, no automatic retry for uncertain executions, and no raw
  data in errors or logs. The client acknowledges receipt of each bounded
  response before the service disconnects, preventing unread bytes from being
  discarded during named-pipe teardown.
   Context snapshot reads add two strict operations and a bounded summary-only
   snapshot response, so the pipe is now protocol v3 (`AIQuantHoldout.v3`).
   Clients must not send v3 frames to a v2 endpoint or retry against an older
   pipe. Service and client upgrades must use the same protocol version, and a
   missing v3 service remains unavailable until the coordinated upgrade is
   complete.
3. Move HOLDOUT database and validation-package ownership into the service;
   remove direct main-API constructors/readers and legacy fallback paths. The
   installer stages protected service-owned copies and A10/A11 contexts use
   summary-only service snapshots, but package preparation and validation result
   storage still use their original local archives. Stop PAPER and research
   writers before first-install SQLite staging to obtain stable snapshots;
   source databases remain in place for recovery.
4. Route A10 validation and A11 stress operations through the service, binding
   exact parent context and task identities to the response receipt.
5. Verify on Windows under the actual service SID that the service can read the
   HOLDOUT store, while a child process under the exclusive interactive account
   cannot open the database, WAL/SHM files, backups, or directory. Verify the
   API can receive only the permitted aggregate response.
6. Verify service restart, timeout, pipe-owner spoofing, malformed messages,
   store corruption, shutdown, backup/restore, and rollback. Independent review
   must confirm the observed token and ACLs before recording isolation as
   established.

Until all checks pass, manifests and validation reports must continue to mark
independent HOLDOUT storage/isolation false. This ADR does not approve live
trading, candidate execution, or promotion.
