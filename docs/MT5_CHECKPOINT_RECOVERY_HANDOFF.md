# Explicit completion of interrupted checkpoint publication — increment 0127

New restores publish a versioned checkpoint-pending.json intent before linking
journals. It binds a publication UUID, original archive hash, connection/schedule
policy, resolved destination path and observed filesystem identity, and exact
prepared journal byte inventories. Native/trading/portfolio authority remains
false. The model/schema is CheckpointPublication /
demo-checkpoint-publication.schema.json.

## Explicit finish

Keep source and destination launchers stopped. Retain the original archive pin
and an independently observed hash of the pending marker. The command never
adopts either hash from its own input artifact:

```text
python -m ai_quant_os.mt5_sync_checkpoint --config <original-config.json> finish-restore --archive <original-checkpoint.zip> --archive-sha256 <retained-archive-sha256> --destination <original-partial-directory> --publication-sha256 <retained-marker-sha256>
```

finish_restore requires an existing destination/marker before acquiring the
existing CLI ownership gate. It verifies the separately supplied marker hash,
strict bounded JSON intent, original policy/archive pin and resolved directory
identity. It revalidates the archive hash, fixed ZIP inventory/internal hashes,
complete journal audits and stable inspection in new owned staging. Prepared
journal bytes must exactly match the original intent; a different SQLite runtime
that changes preparation bytes is withheld rather than admitted by semantic
similarity.

Every present destination journal must match its hash/size and have a single
filesystem link. Unexpected files, symbolic links, shared journals and account
journal WAL/SHM/rollback sidecars are refused. Only the fixed journals, marker and
ownership-lock files are admitted. SQLite's service-lock.sqlite-journal is allowed
while the command holds its gate; SQLite manages that owned rollback sidecar.
Unknown files are never deleted or adopted.

Only missing journals are published through exclusive hard links. The owned
staging name is then removed to admit single-link destination custody. Matching
existing journals remain byte-identical; a racing file creation is withheld
without overwrite. All final hashes/sizes/layout and the unchanged marker are
checked before marker removal. RestoreCompletion binds original archive/marker
hashes, UUID and verified inspection, with all authority false and native
attestation UNVERIFIED. The command launches no service or heartbeat.

## Interruptions and compatibility

A failed finish retains the original marker and any newly completed prefix.
Another attempt requires a new explicit command with the same independent
bindings; no automatic retry/loop exists. Complete matching files with a retained
marker can finish without relinking. After success, repeated finish reports
PUBLICATION_REQUIRED; existing offline service inspection can read the journals.

Legacy bare RESTORING markers lack original commitments and report
PUBLICATION_INVALID. Preserve their directories; do not invent or remove a marker
to force startup. Moved/replaced destinations, changed intent/archive/policy,
altered journals and unknown sidecars remain withheld. Missing marker, corruption,
contention and storage errors have fixed sanitized outcomes. Failure before a
durable marker existed remains an operator custody case, not an admitted finish.

The application guard does not attest hostile filesystem replacement, directory
power-loss durability, launchers ignoring it or direct library writers outside
the CLI gate. Keep launchers stopped throughout completion. This does not fence
source/restored locations, compact capacity, migrate formats, resolve workers or
prove native completeness. Original worker owners, pending identities, audits
and revisions remain archived values; no native process or terminal is adopted.

Successful export/restore results, original checkpoint schemas/journals and the
service's marker-presence guard remain compatible. New interrupted restores
carry richer intents. Rollback retains directories, archives and independent
pins and keeps older launchers stopped on partial state. Native custody,
installed deployment, independent review and P13 acceptance remain pending.

## Verification

Real SQLite/ZIP/filesystem checks cover matching-prefix/revision preservation,
changed file/archive/policy/pin refusal, unknown sidecars/files, legacy intent,
moved directory, actual ownership contention, second interruption, complete files
after failed marker removal and no relink of existing files. A real module
subprocess completes a partial restore without password/native launch, followed
by successful original offline service inspection. Two schemas are additive.
No SDK, terminal, actual credentials, broker, worker reconciliation, portfolio
mutation or live-capital action is used. Focused checks do not establish
full-system acceptance.
