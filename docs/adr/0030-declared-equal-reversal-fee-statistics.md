# ADR 0030: Declare equal reversal fee attribution for research statistics

Status: Accepted for local diagnostic research.

A native directional reversal places one 2Q order with one fixed fee. Closed-trade resampling must attribute it without doubling or omission. Add a declared method, `CIRCULAR_EQUAL_REVERSAL_HALF_FEE_BLOCKS_SHA256_V1`, allocating exactly half to the Q closing leg and half to the Q opening leg. Reject unequal reversals and inexact analytical accounting. Preserve the old method's reversal refusal and canonical serialization.

Require the new method before reserving directional research 1.1/1.2. Bound computation using a possible closed cycle per bar. Reuse the shared source/price/time/quantity/cash verifier for legacy and directional adversarial scenarios; freeze each to the baseline model and source implementation.

Consequences: directional native holdout cycles can support a declared engineering Monte Carlo screen, and existing adverse-market scenarios retain verifiable directional results. No promotion gate is waived. Synthetic closed-cycle resampling does not measure intratrade drawdown, portfolio margin, regime coverage, alpha or live readiness. Preserve source/database artifacts on rollback; older decoders cannot accept the new method.
