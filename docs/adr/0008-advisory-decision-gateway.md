# ADR 0008: Typed advisory decision gateway

## Contract and ownership

`POST /api/v1/paper/decisions/classify` accepts only instrument_id and task_id under operator authorization and audit-integrity checks. It builds the world-state snapshot on the server. Client provider settings and order/risk authority fields are rejected.

The schema 1.0 result specializes the canonical AgentEnvelope fields for RegimeDecision output, with creation time and provider version. It carries task/world-state/input/evidence references, classification, next route, confidence, uncertainty, contradictions, expiry and trace ID. It does not carry risk approval, exposure, broker command or execution capability.

The default provider is absent. A FastClassifier adapter can be injected by server configuration; its ID/version are server-owned. No production Jev service or LLM endpoint is connected. Adapter exceptions are mapped to fixed reason codes so internal provider details do not reach API output.

## Validation and fallback

The optional async primary has a cooperative 200 ms timeout. Its candidate schema forbids extra fields and accepts only TRENDING/RANGING/HIGH_VOLATILITY/UNKNOWN classifications, finite confidence in [0,1], aware timestamps and evidence references. Candidates must bind to the exact input world state and instrument, originate no earlier than that snapshot, be current at completion and cite a nonempty subset of the supplied instrument evidence. External adapters must use bounded I/O, remain cancellation-cooperative and avoid synchronous blocking; the protocol does not sandbox arbitrary provider code.

Absent, failed, timed-out or invalid primary output falls back to the snapshot's deterministic regime. Missing data yields UNKNOWN and WAIT_FOR_DATA. Other classifications route to DEEP_REVIEW. This route is a request for later review; the deep model dispatcher is not implemented yet.

Primary confidence below 0.8 records LOW_CONFIDENCE. Calibration references are recorded as unverified provenance; the gateway records UNCALIBRATED and requires escalation for every current result. Quantitative fallback confidence is zero (unknown calibration). Nominal uncertainty is one minus the provider's declared confidence, not an empirical uncertainty estimate. Disagreement with a known quantitative regime is retained in contradictions.

## Expiry and audit

The result deadline is the minimum of world-state expiry, gateway TTL and accepted candidate expiry. World-state freshness is checked before and after provider execution. API delivery verifies the deadline again after audit persistence. Consumers must call require_current_decision against their expected world-state version and current clock before using an envelope. Expired/future or different-world envelopes are rejected.

Audit events include route, classification, fallback codes, task/world-state references, expiry and trace ID. Task IDs are correlation identifiers, not order idempotency keys. Repeated classification requests compute new snapshots and traces. Envelopes are returned to callers but not stored as a durable decision artifact yet.

## Evidence and remaining work

Tests exercise absence, failure, cooperative timeout/cancellation, invalid schemas, wrong instruments/world states, invented evidence, invalid confidence, expiry before/during/after provider execution, delayed audit persistence, low confidence, unverified calibration and contradictory output. A constructed RiskDecision based on a classifier trace is rejected by the kernel's issued-approval registry. The API creates no orders and rejects authority/provider fields.

P06 remains in progress: production Jev adapter and benchmark harness, validated calibration store/report, durable decision publications, statistical/LLM fallback dispatch and independent review are outstanding. P07 will consume envelopes without granting capital authority. The existing risk kernel and paper gateway remain the execution authority.

Rollback: revert the gateway/API code after stopping the local service. No database schema migration is introduced; classification audit events remain valid with older audit code.
