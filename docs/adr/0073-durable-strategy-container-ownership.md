# ADR 0073: Durable pre-create strategy container ownership

## Status

Implemented metadata reservation and audited acknowledgement history. No Docker
action, source execution or actual creation/removal proof is provided.

## Decision

Bind one opaque owner to each exact prepared prefix job, invocation/stage pin and
intended image/worker pins. Publish its immutable sidecar before committing a
CREATE_INTENT. Future callers issue no create request until both are retained.
Use the existing public kernel ownership context and local audit chain rather
than guessing ownership from PIDs, file absence or mutable Docker tags.

Require exact owner bindings and immutable IDs throughout CREATE_ACK,
REMOVE_INTENT and REMOVED transitions. A missing create acknowledgement permits
only a future independently inspected owned-ID cleanup branch, never a new create
retry. Revalidate canonical records, chain integrity, unique job ownership and
capacity; refuse adoption/reinitialization of damaged existing history.

## Consequences

Record/data acknowledgements are not capabilities or physical runtime evidence.
The journal does not contact Docker or release/delete ownership sidecars/inputs.
Publication and journal commit are separate durable operations and can leave a
retained partial reservation. Historical idempotent returns do not change current
state. The host namespace/writers must follow the cooperative lock protocol.
Independent immutable history remains absent.

The execution parent still needs complete create-recipe and image binding, live
container admission, owned lifecycle/cleanup/recovery and actual confinement/source
attestation. Older strategy/image schemas, worker, archive identities and research/
repair routes remain unchanged. Rollback withdraws the new reservation helpers and
retains unknown ownership for inspection. See STRATEGY_CONTAINER_OWNERSHIP_HANDOFF.md.
