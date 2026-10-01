# ADR 0019: A02 version 0.2.0 shadow regime mission

## Decision

Install a real A02 LangGraph-compatible mission handler under immutable version 0.2.0. Retain all original 0.1.0 planned manifests and their hashes. The new manifest is PARTIAL and starts in SHADOW: source integration, calibration and promotion are unfinished.

The mission uses deterministic features, separately implemented in regime_features. It computes 8-, 24- and 64-bar windows with actual observed spans. SHORT needs at least three bars; MEDIUM and LONG require their full windows. These are bar-count horizons, not assertions about minute/hour/day duration. Each window reports net return, realized population volatility and path efficiency through its source context.

The context adapter reads immutable bounded histories and verifies their bar-content hash against the exact world snapshot. A concurrent history change produces a checked context failure instead of mixing versions. Price, volatility and session inputs include the world version. Calendar, event and crisis sources remain explicitly UNAVAILABLE; UTC observation time is the only connected session fact.

## Outputs and uncertainty

The shared mission AgentEnvelope v2 contains exact agent version/task, input and evidence references, confidence, uncertainty, expiry, contradictions and trace ID. Existing specialized fast/deep v1 wire envelopes remain unchanged. The graph validates mission-envelope identity, source references and time bounds against the admitted context before accepting completion.

The RegimeDistribution output includes each instrument and all three horizons. Missing instruments/windows return UNKNOWN rather than inheriting another horizon's result. Each available window normalizes trend strength `abs(return_bps)/20 * efficiency`, range strength `1/(1 + abs(return_bps)/20)` and volatility strength `volatility_bps/25`. Normalized strengths are explicitly UNCALIBRATED_FEATURE_SCORES, not validated class probabilities. Twelve-place Decimal weights sum exactly to one regardless of serialization key order. The largest strength gives the descriptive regime.

Confidence is zero, uncertainty one and escalation always required until empirical calibration exists. Gap codes disclose unavailable calibration/session/event/crisis evidence. HORIZON_DISAGREEMENT identifies differing available window labels. These codes do not grant risk, broker or recovery authority. No provider is called by this deterministic route; existing Jev/deep-review adapters remain separate pending benchmarked integration.

## Evidence and promotion

The bounded synthetic replay runner invokes the actual graph and handler against five cases: trend, range, alternating volatility, horizon disagreement and insufficient history. Its report records the fixture SHA-256, predictions, per-case latency and source context references. The saved development baseline has 15/15 expected window labels. These constructed scenarios test behavior; they are not held-out market data, evidence of trading performance or calibration. Promotion remains unapproved.

## Remaining A02 requirements

Connect trusted market sessions, event and crisis evidence; add their classifiers and evaluation datasets; implement configurable model routing and approved Jev/deep-review escalation; establish empirical calibration, health/cost metrics and independently reviewed shadow acceptance. Persist lifecycle state and provide an evaluated working rollback target before promotion. The 0.1.0 planned manifest is retained for historical compatibility, not claimed as a working rollback implementation.
