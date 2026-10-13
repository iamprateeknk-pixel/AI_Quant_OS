# Candidate shadow portfolios

P09 now has a durable local portfolio tracker for a frozen research candidate. It computes cumulative hypothetical fills, position and PnL through isolated native engines. All assignments and reports have `promotion_allowed=false`; tracking does not clear research gates, approve a paper strategy, or authorize any broker order.

## Assignment and observations

Assignment retains the complete candidate dossier, source objects, genome, quantity/cost policy and the registry eligibility snapshot. The candidate must be an unchanged challenger with a native holdout result. View and bundle are read in one registry transaction. The cutoff cannot precede the holdout's last observation. The command generates its assignment time from the current UTC clock and preserves it on identical retries.

Every observation is strictly after the cutoff and the last stored bar, on the candidate's instrument. Duplicate, reordered, overlapping and future-dated observations are refused. At least two bars are required for the first batch; later batches may append one bar. Each portfolio retains at most 4,096 cumulative bars. It starts flat without pre-cutoff warmup or inherited paper positions.

Each batch runs a fresh native BacktestEngine over the entire accumulated series. Earlier fills must reproduce exactly; newly added fills must occur after the previous observation period. This preserves causal histories and cumulative positions across process restarts without copying private native engine state. It costs repeated prefix replay and is a bounded local prototype, not a continuously running market service.

The native bar model executes from prior completed history at the next synthetic bar-open quote with declared costs, fixed quantity and final-close marking. Native execution prices, fee cash flows, long/flat position and PnL reconcile against retained bars through a shared verifier also used by counterfactual reports. This does not model recorded spread, partial liquidity, latency, intrabar paths or live broker fills.

## Lifecycle and failure handling

Candidate eligibility is checked before new work and after native execution. Withdrawal blocks new observations and is detected during an in-flight run; a withdrawal failure retains a valid completed native run for investigation. Earlier completed requests remain retrievable after withdrawal. The candidate's own lifecycle, validation gates and original dossier are never changed by shadow tracking.

Source preparation, native failure, changed historical fill prefix and candidate withdrawal produce sanitized failed reports. Earlier reports remain intact. Invalid native evidence or unexpected/storage failure leaves an uncertain reservation. Failed or unfinished observations block subsequent batches; there is no automatic retry under another command ID or implicit state reset. Recovery and retirement workflows remain unfinished.

The candidate registry and shadow database are separate. Eligibility checks are not an atomic transaction with candidate withdrawal: withdrawal immediately after the final check may precede archive commit. No capital authority exists in that interval; the next observation refuses the changed lifecycle. A future execution assignment needs one transactional lifecycle authority and an independent review.

## Storage and integrity

`shadow.sqlite3` is a separate sibling database. An immediate transaction reserves each immutable command before native execution. Only its private owner can finish once. Reservations are sequential per portfolio; concurrent requests have one owner. Completed matching commands return cached evidence without another engine run; altered command payloads are refused.

Reports retain their full source series and assignment, sequence, previous report hash, capture time and native ledger. Reads verify typed canonical SHA-256 artifacts, exact cumulative source lineage, chronological capture time, prior fill prefix and the committed head. Completion and head advancement commit atomically. Preserve the SQLite database/WAL for investigation or rollback.

Limits: 1,000 assignments, 1,000 total batch reservations, 64 MiB aggregate stored assignment/request/report bytes, and 16 MiB per encoded artifact. Repeated full parent/source retention can exhaust capacity before the numerical limits. No implicit pruning occurs.

Trusted built-ins run in the host Python process. Native accounts isolate simulation state; they are not a security sandbox for generated code. Local callers and the machine clock are trusted. The library supports explicit logical cutoffs for reproducible historical fixtures; those are diagnostic replays, not contemporaneously collected forward evidence. Checks detect inconsistent local records, not host administrators replacing records, hashes or the entire database. Independently attested market/capture timestamps, raw ingress lineage and cross-portfolio period exclusion remain unfinished.

## Commands

Install the editable package with research/trading prerequisites. Register the candidate, then:

```powershell
ai-quant-shadow --workspace data-local/research-adversarial-a assign eurusd-shadow --candidate-id aa8c5d53f15133b8b1b391af0123a9b50cd047e067112e989fa56bef5126d4c6
ai-quant-shadow --workspace data-local/research-adversarial-a observe eurusd-shadow --command-id first-observations --bars new-observations.csv
ai-quant-shadow --workspace data-local/research-adversarial-a latest eurusd-shadow --output data-local/shadow-report.json
```

Use the full verified candidate ID from the registry. Observation CSV columns match the existing bar importer: `instrument_id,observed_at,open,high,low,close,volume`. Supply newly completed observations after assignment, with timezone-aware UTC timestamps. The command does not accept a backdated assignment cutoff, source code, promotion claim, filesystem database override or broker route.

Exports use immutable atomic publication, protecting database/WAL/SHM paths and archive/strategy subtrees. No HTTP endpoint or new agent tool permission is added. Console output and retained reports are the current operator surface; Mission Control integration remains unfinished.

## Verified fixture and remaining P09 scope

Two independent workspaces each ran two native cumulative windows and exported identical first/final reports. The synthetic logical-clock fixture retains three then six EURUSD bars after its archived holdout. The first window has an open 100-unit position and -0.10 USD marked PnL; the second ends flat with -20.20 USD PnL. Both saved reports validate with the report schema, typed decoding and canonical reencoding, and their sequence/hash/prefix chain replays from SQLite. This proves the mechanism under controlled inputs, not prospective performance or independent calibration.

P09 still requires production shadow ingress/scheduling, lifecycle recovery/retirement, candidate-bound paper assignments, live/shadow comparison, linkage to the separate daily opening-mark/accounting workflow, agent attribution, recorded fill-time/latency evidence, reality-gap alerts and demotion. Research shadow, sandbox and independent-review gates remain pending. Hosted CI is configured but unverified. No paid provider or live broker was exercised.
