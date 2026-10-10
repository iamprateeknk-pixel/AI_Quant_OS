# ADR 0132: Replay asynchronous recorded quotes in one native USD account

## Decision

Add an explicit recorded-quote portfolio model alongside the synchronized bar
model. Canonical source ordering caches all equal-time quotes before prior-history
decisions, using a declared one-nanosecond dispatch priority. Preserve original
source timestamps and exact recorded bid/ask execution in retained evidence.

Require fresh source quotes for new gross exposure and final valuation. Permit
active-instrument risk reductions with only its own fresh quote. Admit against
shared native USD collateral, with conservative JPY notional conversion and
explicit quote-age policy. Fees remain USD; quote P&L and signed final FX
conversion are separate from the observed native balance.

## Boundaries

Only trusted registered tick builtins run on the host. Use the existing native
bridge, source validator and fill reconciler; the optional fill reconciliation
fee policy preserves the default single-instrument quote-fee behavior. Reuse
the immutable archive writer for exports. Add two schemas and a distinct packaged
command; existing schemas and bar reports remain unchanged.

## Verification and limits

Actual native fixtures cover shared collateral refusal, stale exposure refusal,
active risk reduction, JPY valuation with USD fees, equal-time quote availability,
prior history and canonical replay, retained amount/fill/total tampering, and the
immutable command export. The retained verifier is a source/cash check, not an
attestation of native balance, strategy correctness or historical admission.
The handoff and machine evaluation describe fresh regression evidence.

No margin/liquidation, broker authority, candidate execution, source custody
certification or independent critical acceptance is inferred. All phases remain open.
