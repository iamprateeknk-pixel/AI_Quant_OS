# 0096: Protected observations of archived agent genomes

Date: 2026-10-09

## Decision

Read existing genome archives through the current operator session. Keep lineage,
objectives and source fingerprints as metadata; never instantiate or execute a
challenger, expose prompts, or interpret a retained objective as an evaluation result.
Check every retained genome and parent policy under a single SQLite snapshot and bind
pagination to that snapshot's audit head. Missing archives are unavailable and are not
created by the route.

Use a bounded metadata projection. Preserve an exact decimal when it fits; otherwise
omit it with an explicit preview flag and refer to the unchanged original archive.
Reuse a private protected server read for health, research and evolution, preserving
the literal local upstream restriction, session cookie boundary and byte/time limits.

## Consequences

The frontend exposes real archival history without execution or promotion authority.
The view grants no champion, freeze, rollback or evaluation admission capability.
Original genome and prior HTTP schemas remain unchanged. Browser acceptance is
pending because policy rejected the local preview launch. Full-plan completion and
all release gates remain unproven.
