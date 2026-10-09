# Strategy implementation archive view

`/strategies` displays existing protected implementation archives from the configured
`AI_QUANT_RESEARCH_WORKSPACE/strategies` directory. Metadata includes exact versioned
references, input kind, parameter defaults and implementation/source fingerprints.
Saved implementation state is `ARCHIVED_IMPLEMENTATION`, with promotion authority
false. Registration does not prove validation, eligibility or operating lifecycle.
No source content or operator session nonce is rendered.

The read-only `GET /api/v1/research/strategies/page` verifies all retained manifests and
their source bytes before paging. The catalog fingerprint covers ordered semantic
implementation identities; continued pages require it. Manifest inventories are
observed before and after verification; observed change or a stale requested head
returns 409. Integrity errors and unavailable archives return sanitized 503 responses.
Missing directories are not created. A valid empty directory returns an empty catalog.
This is a filesystem observation under the existing immutable archive convention,
not a transactional audit journal or protection against a hostile host administrator.

Catalog reads are capped at 2048 directory entries, 1000 manifests, 8 MiB of manifest
bytes and 32 MiB of checked source bytes. Pages contain at most ten bounded previews.
Source names longer than 128 characters are explicitly marked shortened; original
manifests and fingerprints are unchanged. No strategy is instantiated by the read.
The view also works without the optional native backtesting package; tested saved
demonstration implementations require only the base package for metadata reads.

## Verification

Failing endpoint and parser checks preceded implementation. Checks cover protected
access, page identity, off-page source corruption, changes during observation, empty
and missing archives, inventory capacity, metadata/schema/body bounds and optional
native-package absence. Frontend projection rejects approval claims and invalid
pages. Production build/types, lint and exact staged verification are recorded in
`evaluations/strategy-archive-ui-0097.json`.

Browser acceptance remains pending. The previous local preview launch was rejected
by execution policy; it has not been bypassed or retried through another launch path.
No keyboard, mobile or rendered privacy success is claimed for this view.

## Remaining full-plan scope

Live/Shadow/Paper/Candidate/Retired operating tabs, measured metrics and reality gap,
regime suitability, allocation history, per-strategy validated dossier associations,
audited pause/resume requests, A12 selector integration and independent acceptance
remain pending. P00-P13 and their release gates remain incomplete.
