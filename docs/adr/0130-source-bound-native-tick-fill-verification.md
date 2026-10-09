# ADR 0130: Verify recorded-tick fills before returning native results

## Decision

Bind native recorded-tick results to a captured canonical input and verify exact source prices, full target quantities, fees, event order, cash reconstruction and final midpoint mark before returning from the existing runner. Reuse the same source validation at admission and evidence verification. Preserve the native pin and all existing wire models.

Place retained evidence reconciliation in a separate module with no native SDK or strategy execution dependency. Keep strategy target correctness, native realized P&L, native custody and release authority outside its claims. Reject duplicate source rows rather than silently deduplicating ambiguous input.

## Reason

The old runner compared the native position with callback totals but calculated result cash directly from unverified callback fills. A controlled native callback with an altered execution price produced a result; duplicate input rows also passed. Both failures were reproduced before the verifier and shared source gate were introduced.

## Consequences

Source-inconsistent native results cannot be returned or exported. Existing valid next-quote spreads, short reversals, quote-currency fees, archive replay and CLI behavior remain compatible. A source-matching cash ledger remains evidence of that ledger, not proof of a strategy decision trace or trading eligibility. Full P03/P13 acceptance remains unproven.
