# Offline demo sync checkpoint — increment 0124

The checkpoint command exports the existing gateway, projection and controller
journals into a new archive, and restores an independently pinned archive into
a new state directory. It performs no capture, worker reconciliation, timer,
heartbeat publication, order submission or portfolio update.

## Operator flow

Stop all launchers using either source or destination. Retain the original
private configuration outside the archive. Use the existing local Python
environment with the package installed, or its module entry point:

```text
python -m ai_quant_os.mt5_sync_checkpoint --config <original-config.json> export --state-dir <existing-state> --archive <new-checkpoint.zip>
python -m ai_quant_os.mt5_sync_checkpoint --config <original-config.json> restore --archive <checkpoint.zip> --archive-sha256 <independently-retained-sha256> --destination <new-state-directory>
```

The packaged entry point is `ai-quant-demo-checkpoint`. Restore takes the hash
as a separate required argument; it never adopts a hash supplied by the archive.
Retain that hash independently before relying on the artifact for recovery.
The original connection and schedule must match, including enrollment and
symbol mapping. Password-independent policy identity allows these offline
commands to use a noncredential placeholder; no broker is authenticated.
Capture and recovery retain their existing password and launch requirements.

Do not run the original and restored directories concurrently. This copies
observational state; it creates no fencing lease between locations and grants
no new native worker or terminal custody. Restored uncertain attempts keep
their original owner/worker identifiers and remain withheld. A restored old
healthy cursor may require the existing explicit history-gap recovery.

## Export and admission

All three journals must already exist. The command takes the existing CLI
ownership lock, validates their audited current state, then acquires SQLite
writer transactions across all three files before copying them through SQLite's
backup API. This includes committed WAL content and prevents cooperating SQLite
writers from changing the frozen snapshot. Locks close without application
writes. Copied audits/policy are verified before publication; stable state is
compared with source inspection before and after the copy. Clock labels and
expired quote/report facts do not replace durable commitments.

Each journal is bounded to 96 MiB, the manifest to 32 KiB and the archive to
three journal bounds plus 64 KiB. Each SQLite backup has a 20-second deadline.
The manifest carries original policy identity, exact journal sizes/hashes,
bounded inspection and fixed denied authority fields. The archive includes
exactly manifest.json and the three named journals, using stored ZIP members.
It excludes configuration, credentials, service lock and previous heartbeat.
Journals include retained account/deal observations: keep this private artifact
under the operator's existing local filesystem access boundary.

Export builds in a newly owned directory, flushes/fsyncs the complete archive,
and publishes through an exclusive hard link. Existing archives are refused.
Only known temporary files from that owned directory are removed. This requires
a local filesystem supporting hard links; there is no overwrite/copy fallback.
Directory power-loss durability and hostile filesystem replacement are not
attested. SQLite constructors may manage journal sidecars; application audit
history, revision, pending identity and heartbeat remain unchanged.

## Restore and interrupted publication

Restore checks the independently supplied archive hash before unpacking and
again after validation. It accepts only the four fixed members, rejecting
duplicates, extra/path-traversal names, compression, encryption, oversized data,
duplicate JSON fields, mismatched internal hashes and changed policy. It never
uses ZIP path extraction. Exact bytes are copied into a newly owned staging
directory; the original journal validators then verify their complete audit
chains and stable inspection against the manifest.

Only after validation is a new destination reserved. The command takes its CLI
ownership gate, publishes a flushed checkpoint-pending.json marker, and links
the three completed snapshots into that directory. After all files are present,
it removes its marker. A publication failure retains the destination and marker;
restore refuses to overwrite or automatically retry that directory. The service
CLI refuses CHECKPOINT_INCOMPLETE before opening journals when the marker exists.
Preserve partial state for operator diagnosis; do not remove the marker to force
a launch. Export likewise refuses an incomplete checkpoint source.

Launchers must stay stopped throughout migration, including directory reservation.
The marker is an application guard, not filesystem access control or a guarantee
against launchers that ignore it. Direct library writers are outside the CLI
guard; SQLite writer locks protect the actual export snapshot. This does not
compact history, reset capacity, resolve custody, change configuration or migrate
old event formats.

## Verification and remaining acceptance

Checks exercise real SQLite journals and ZIP artifacts, exact saved revision and
deal counts, independent hash/policy admission, corrupt audits, extra/duplicate
members, altered journal commitments/inspection, capacity refusal, existing
destination preservation, writer exclusion, partial publication retention and
service startup refusal. Real module subprocesses export and restore without a
password or native work. Uncertain worker journal rows and pending identity remain
unchanged. No SDK, terminal, real credentials or external broker is exercised.

Existing schemas/journal formats are unchanged; the checkpoint schema is additive.
Rollback retains original journals, archive/hash and restored/partial directories.
Older service versions lack the incomplete-checkpoint guard: stop launchers before
rollback and never launch a partial destination. Native custody/completeness,
capacity rollover, installed service deployment, independent broker review,
browser acceptance and all P13 release gates remain open.
