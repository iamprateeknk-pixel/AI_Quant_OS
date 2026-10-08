# Research dossier UI handoff

Date: 2026-10-09

## Implemented

`/research` reads protected archived candidates through the existing server-owned
operator session. It displays lifecycle/revision separately from research outcome,
selected strategy references, hypotheses, recorded folds/attempts, gate results,
reason previews and original report/plan/event fingerprints. CHALLENGER explicitly
means incomplete validation. Promotion remains unauthorized for every outcome.

The new typed `GET /api/v1/research/candidates/page` endpoint verifies the candidate
journal and reads the selected original bundles under one SQLite transaction. Each
selected bundle undergoes canonical typed decoding, original report/manifest binding
and source inventory validation before projection. Candidate journal changes invalidate
continued pages through the original head hash. Existing list/bundle/register/withdraw
wire contracts are unchanged. Native dependencies still load only for enabled archive
requests; base paper API startup remains usable without them.

Pages contain at most two dossiers. Previews are bounded to 1,024 hypothesis characters,
16 gates with 64-character names and 256-character reasons, and 16 report reasons of
256 characters each. `details_truncated` explicitly marks shortened text or inventories.
The original dossiers are retained without changes and remain available through the
existing protected bundle endpoint/CLI. Preview output never contains source text.
An escaped-control-character fixture verifies that a full two-row preview fits the
128 KiB protected-read bound. Empty, missing sign-in, invalid/unavailable and changed
snapshot states withhold invented outcomes. The page performs no research run,
candidate withdrawal, promotion or capital action.

## Verification

- Missing endpoint and frontend parsing were reproduced before implementation.
- All 23 frontend checks passed; final production build/type checks and lint passed.
- Research archive/page/lazy-dependency/session checks passed 28 tests in 22.68 seconds.
- Installed Edge verified the final production page against three explicitly synthetic
  archived fixtures: two/one record pages, keyboard gate inspection, changed-head
  rejection, 390px layout, absent source text and session nonce, and hidden records
  after logout. No browser page errors were observed. No proposed source was executed.
- A preview-bound mismatch was caught by the worst-case transport test and corrected;
  the original test was preserved. A browser selector matched SANDBOX both in a reason
  list and gate row; it was narrowed to the actual row header. Final checks passed.
- Both test-owned servers were stopped. Fixtures/screenshots remain under
  `data-local/research-dossiers-0095`.

## Remaining requirements

Hypothesis backlog management, active experiment/job displays and controls, dataset
selection, detailed result plots and currency-specific metrics, full dossier download
in the UI, spend tracking, realtime updates and independent acceptance remain open.
Only selected bundles receive full typed report/source validation on each page read;
all registry records receive the existing journal/fingerprint checks. Paging detects
journal changes rather than retaining historical page snapshots. Preview text is not
the full dossier or an independent acceptance record. The full P00–P13 build remains
incomplete; scoped checks do not establish full-suite or production acceptance.
