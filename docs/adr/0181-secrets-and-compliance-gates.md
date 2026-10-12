# Record secrets and compliance release gates

## Decision

Document the credential/configuration surfaces visible in the local paper code and define the minimum owner-reviewed gates before staging or shadow. The inventory names environment variables only and does not inspect ignored local environment files or values. It treats provider and database connection strings as potentially secret-bearing.

## Consequences

- Current operator/recovery tokens and optional provider/demo credentials have explicit handling requirements and source anchors.
- Local `.env` ignore rules, cookie flags, provider spending controls, and demo configuration checks are not represented as production identity, secret management, legal approval, or complete network security.
- Data rights, jurisdiction, privacy/retention, cost correspondence, rotation, and external security review remain owner decisions and P00/P04/P12 gates.
- Nothing is enabled or transmitted by this documentation change; P13 stays blocked.

## Verification

Documentation inventory was checked against configuration reads in `api.py`, `operator_session_api.py`, `jev_adapter.py`, `mt5_sync_service.py`, data/cache/workflow clients, the web operator-session boundary, `.gitignore`, and the S3 integration fixture. No environment values were read.

## Rollback

Revert the inventory, handoff link, ADR, and status entry together. No runtime setting or credential is changed.