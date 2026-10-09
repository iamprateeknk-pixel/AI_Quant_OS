# ADR 0131: Retain closed native position cycles before netting cache reuse

## Decision

Retain native closed-position amounts in the trusted replay bridge, validate their inventory against closing/reversal fills, and include them with current open-position realized amounts. Require agreement with the observed native account balance before a result is returned. Use the same correction for bar and recorded-tick replay.

## Evidence

Actual native reversal/flat and reversal/open fixtures reproduced missing earlier closed P&L under the old `cache.positions()` sum. The installed and pinned upstream position source shows reused closed positions clear prior lifecycle state. Adding closed events while also summing closed cache positions would double count the final cycle; therefore only currently open cache positions supplement retained closed events.

## Historical compatibility

Preserve all original result schemas, source/fill/cash models and retained artifacts. Corrected observed P&L changes current report hashes, so publish separate corrected baselines and retain the original provenance. Counterfactual reproduction of an original cache-only parent remains refused instead of silently reinterpreting archived evidence. No automatic migration, lifecycle approval or release authority is added.
