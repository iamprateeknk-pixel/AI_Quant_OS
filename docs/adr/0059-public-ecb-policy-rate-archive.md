# ADR 0059: Public ECB policy-rate capture and immutable local replay

Date: 2026-10-03

## Decision

Add explicit public ECB intake to the existing data command, with a fixed HTTPS
query for three policy-rate series and their last two observations. Separate
strict normalized facts, bounded network retrieval, atomic archive and commands.
Preserve provider date-of-change semantics and UTC receipt time separately.
Current applicability and historical-vintage availability remain unverified.

Retain complete original CSV and normalized receipt with a local publication
journal. Replay reconstructs facts rather than trusting normalized JSON alone.
Reject source/dimension/unit/status ambiguity, malformed or excess observations,
unbounded responses, redirects, timeouts and missing archive integrity metadata.
Disclose local CSV import separately from the actual tested HTTP retrieval path.

## Evidence and limits

An actual public HTTPS request produced a retained 2,421-byte CSV, six dated
observations, exact raw/receipt identities and a typed schema. Source pins cover
four named modules. Focused tests use controlled HTTP boundaries and real SQLite
and command subprocesses. Full regression evidence follows in the build status.

The local journal has no independent external anchor, automatic retention or
distributed ownership. A03, other currencies, yield/release/text feeds, genuine
historical vintages, production scheduling/calibration and promotion remain open.
No paper control, order gateway, sealed implementation or agent registry changes.

## Rollback

Preserve the independent rate database and retained artifacts, then revert.
Older commands cannot read rate captures; retain a compatible reader. There is
no shared database migration or required local app restart.
