# Secrets and compliance release gates

## Current scope

The checked-in service boundary is a local PAPER implementation. This inventory describes credential names and data flows visible in source; it does not inspect local ignored environment files or disclose values. No credential value belongs in this document, tests, prompts, logs, commits, or frontend bundles.

## Credential and connection inventory

| Configuration | Current use | Required handling before external use | Evidence |
| --- | --- | --- | --- |
| `AI_QUANT_OPERATOR_TOKEN` | Authorizes local operator actions and seeds backend-owned operator sessions | Replace local shared-token assumptions with named production identities, MFA, session revocation, rotation, and audited least privilege | `src/ai_quant_os/api.py:create_app`; `src/ai_quant_os/operator_session_api.py:OperatorAuthorization` |
| `AI_QUANT_RECOVERY_TOKEN` | Distinct local credential for paper recovery | Keep separate from ordinary operator identity; establish dual-control owner, protected storage, rotation, and recovery drills | `src/ai_quant_os/api.py:create_app`; `docs/SECURITY_BOUNDARY.md` |
| `AI_QUANT_JEV_API_KEY` | Optional external classification provider; disabled by default; paper features may be sent when explicitly enabled | Secret manager, restricted provider project, spend ceiling, data-transfer review, rotation/revocation, and tested provider outage behavior | `src/ai_quant_os/jev_adapter.py`; `src/ai_quant_os/provider_spend_store.py`; `README.md` provider integration notes |
| `AI_QUANT_REVIEW_API_KEY` | Optional external review provider; disabled by default; compact paper evidence may be sent when enabled | Secret manager, restricted provider project, spend ceiling, data-transfer/retention review, rotation/revocation, and tested provider outage behavior | `src/ai_quant_os/api.py`; `src/ai_quant_os/provider_spend_store.py`; `README.md` review-provider notes |
| `AI_QUANT_MT5_PASSWORD` | Optional MetaTrader demo observation credential read from a fixed environment name; service config rejects embedded passwords | Use a demo-only account, isolated service identity, secret-store injection, rotation, restricted host/network, and verified child-process/environment isolation | `src/ai_quant_os/mt5_sync_service.py:load_config`; `src/ai_quant_os/mt5_sync_service.py:run_service` |
| `AI_QUANT_DATA_DATABASE_URL`, `AI_QUANT_VALKEY_URL`, `AI_QUANT_TEMPORAL_TARGET`, `AI_QUANT_OTEL_ENDPOINT` | Optional database, cache, workflow, and telemetry destinations | Treat connection URIs as secret-bearing; require TLS where supported, authenticated endpoints, network allowlists, bounded access, and redacted diagnostics | `src/ai_quant_os/data_cli.py`; `src/ai_quant_os/local_cli.py`; `src/ai_quant_os/api.py`; `src/ai_quant_os/paper_telemetry.py` |
| `P02_TEST_S3_ACCESS_KEY`, `P02_TEST_S3_SECRET_KEY` | Explicit S3 integration-test credentials only | Use disposable least-privilege test credentials and isolated test buckets; never reuse staging or production credentials | `tests/test_s3_http.py` |

The repository ignores `.env` and `.local-stack.env`, and web operator sessions use an HTTP-only, strict-same-site cookie with a local-origin boundary. Ignore rules and cookie flags reduce accidental exposure but do not provide secret scanning, endpoint security, TLS termination, identity proofing, or production secret management. Evidence: `.gitignore`; `apps/web/src/features/operator-session/server.ts`; `apps/web/src/features/operator-session/boundary.ts`.

## Required gates before staging or shadow

- Name a security owner and data/compliance owner for each destination environment.
- Record the intended users, internet exposure, network paths, data residency, retention, and incident contact.
- Keep credentials in an approved secret manager; define who can read/rotate/revoke them and test rotation without logging values.
- Separate local, staging, shadow, and live identities, credentials, keys, databases, and provider projects. Do not promote `.env` files or credentials between tiers.
- Confirm the provider and broker/demo service terms, the allowed purpose and jurisdiction for market/account data, data redistribution limits, and retention/deletion obligations with the responsible legal/compliance owner.
- Review whether operator and account data contain personal or regulated information; document minimization, access, retention, deletion, and breach response before remote storage.
- Verify egress allowlists, TLS, authentication, rate/cost limits, log redaction, and monitoring for each external destination.
- Verify backup/restore, audit retention, revocation, outage handling, and shutdown procedures. A local hash chain does not replace independent immutable audit storage.
- Obtain independent P00/P04/P12 security and operations acceptance before continuous shadow; keep live disabled until all signed P13 evidence is complete.

## Current gaps

Production identity and MFA, centralized secret management, rotation and revoke drills, deployment network controls, verified remote retention, data-use approvals, jurisdiction/account eligibility decisions, and independent security acceptance are not established by the local code or tests. Optional provider cost journals do not prove invoices or cost correspondence. Demo observation does not establish broker permission or native execution eligibility. These gaps keep staging/shadow acceptance conditional and live release blocked.

## Evidence anchors

- `src/ai_quant_os/api.py:create_app` reads operator/recovery tokens and wires optional providers and stores.
- `src/ai_quant_os/operator_session_api.py:OperatorAuthorization` compares operator credentials and validates backend-owned sessions.
- `src/ai_quant_os/jev_adapter.py` and provider spending modules bound optional provider calls; external billing correspondence remains unverified.
- `src/ai_quant_os/mt5_sync_service.py:load_config` rejects password-in-config and reads the demo password from a fixed environment name.
- `apps/web/src/features/operator-session/server.ts` sets session-cookie flags; `boundary.ts` constrains local request destinations.
- `.gitignore` excludes local env files; exclusion does not protect a compromised workstation or an already-published credential.