# Daily PAPER and candidate shadow comparison

The protected comparison endpoint links one published daily PAPER execution report to a consecutive pair of retained candidate shadow reports. It reports fill counts and exact quote-currency cash and fee flows for the matching candidate, genome, strategy, instrument, and UTC day.

## API

`POST /api/v1/paper/evidence/daily/{day}/shadow-comparison` requires the operator token and a strict JSON body:

```json
{
  "shadow_id": "eurusd-shadow",
  "sequence": 2
}
```

The referenced sequence must be at least 2. The prior report must end before the UTC day; the selected report must be its immediate hash-linked successor, and all bars in its appended batch must fall within that day. PAPER source data comes only from the immutable daily execution evidence archive. Shadow data comes only from the verified shadow archive. The endpoint returns `no-store` evidence and recomputes it on each request; it does not accept source amounts, fills, prices, or timestamps from the caller.

The response includes hashes for the daily PAPER artifact and both shadow reports, candidate/genome/strategy/instrument identities, fill counts, actual fill cash flow, fee cash flow, and shadow-minus-PAPER differences. Cash flow is `-signed quantity × fill price - fee`, in the retained quote currency.

## Interpretation and limits

The report compares observed fills in a retained UTC interval. It does not assert complete market coverage, compare marked portfolio equity, assign causality, recommend a strategy, or permit promotion. A zero-fill side is retained as zero only when its immutable daily or shadow source contains no fills; absent reports and mismatched intervals are errors, not zero activity. Financing, restatements, fill-time quotes, latency, and live broker evidence remain outside this report. All execution routes stay PAPER.

The output schema is `docs/schemas/paper-shadow-comparison-v1.schema.json`; request schema is `docs/schemas/daily-shadow-comparison-input-v1.schema.json`. Focused tests exercise exact source linkage, daily cash/fee arithmetic, mismatched intervals, the protected API projection, and schema parity.
