# Record environment strategy and tier gates

## Decision

Document a four-tier roadmap: local development, AWS development/staging, shadow/paper, and restricted live. Each tier has allowed authority and explicit entry evidence. Current status is local paper development only; staging is unselected and unaccepted, continuous shadow is unaccepted, and live remains disabled without a release owner.

## Consequences

- The execution-pack environment sequence is explicit without selecting, provisioning, or funding a remote environment.
- Secrets, provider costs, data rights, identity, isolation, recovery, monitoring, and rollback are named as tier-entry evidence.
- Operators must validate the intended environment, users, owner, and internet exposure before treating staging design as final.
- `PromotionDecision` remains fail-closed and P13 remains the only live-release authority.

## Verification

This is a documentation-only planning decision checked against the execution pack environment notes, project release policy, control ownership, and current paper-only source boundary. It does not claim external deployment or approval.

## Rollback

Revert this roadmap and ADR together. No runtime configuration, credentials, or environment is changed.