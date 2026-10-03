# 0055: Separate binary application dependency profiles

Date: 2026-10-03

## Decision

Use uv 0.11.29 to resolve all application extras for CPython 3.11, separately for
Windows amd64 and Linux x86_64/glibc 2.35. Retain exact requirements and hashes,
including local package build tools. Bootstrap uv through a separate hash lock.
Install the reviewed editable application without dependency resolution or build
isolation after installing the locked binary distributions.

The dependency declaration fingerprint deliberately excludes application
entrypoints and source bytes. Those are checkout/release concerns. Lock checks
refuse declaration drift, retain existing versions during re-resolution and
compare complete bytes without writing. Regeneration publishes an individual
profile only after successful resolution. Lock files keep LF on all checkouts.

## Consequences

The approved repair-image profile is unchanged. The application profile includes
larger native dependencies and is not admitted as a repair-worker image. This
decision does not execute candidate sources, grant broker authority or enable
paid providers. It does not pin the operating system, Node runtime or deployment
image, or claim support for other Python versions/architectures.

The new Windows/Linux CI gate verifies locks, installs hashes, builds the frontend
and runs the full suite. Existing specialized jobs remain separately configured.
Actual hosted CI and Linux runtime results must be obtained before asserting
deployment compatibility.

## Evidence

Fresh Windows environment `data-local/locked-windows-0055` excludes system
packages. Binary hash installation and a dependency consistency check succeeded.
The separately hash-installed compiler reproduced both retained profiles exactly.
Focused tests cover declaration drift, missing/unknown profiles, source-entrypoint
independence, failed resolution, check-only publication refusal and the fixed
binary/target/index policy with runtime downloads disabled. Full regression
results and remaining limitations are recorded in the dependency handoff:
1,035 passed/15 skipped in the full run and all ten final dependency tests passed.
Both locks also reproduce from a fresh Windows index checkout.
