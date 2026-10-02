# ADR 0049: Static offline dependency image assembly

Date: 2026-10-02
Status: Accepted for OCI artifact construction; runtime acceptance pending

## Decision

Assemble the dependency-only image from verified pinned base blobs and locked
wheels without a daemon. Verify both compressed hashes and expanded `diff_ids`.
Use pinned PyPA installer 0.7.0 for layout/launcher/RECORD generation, with no target
imports, hooks or bytecode compilation. Normalize cross-scheme RECORD paths for
Linux and construct root-owned fixed-mode/timestamp layer entries. Retain original
base layers and emit an OCI layout archive, config/manifest hashes and a final
input/output inventory receipt with no admission/promotion authority.

The fixed assembler does not interpret Dockerfile instructions. Its recipe hash
is provenance context only. It trusts operator inputs and the host toolchain;
archive validation is not sandbox enforcement or an independent certificate.

## Consequences

Actual image bytes can be prepared while Docker is unavailable. Two fresh local
assemblies were byte-identical; independent OCI schema/blob/rootfs inspection
passed. Windows ZIP original filenames must be inspected before host normalization;
zero-length package files remain valid. Cross-host/OS reproducibility, native Linux
behavior, Docker import/admission and security/behavior acceptance remain open.
The assembler does not fix the unavailable daemon or weaken its confinement gates.
Disable/revert the builder to roll back while retaining artifact/source evidence;
no candidate registration, incident closure or capital authority follows assembly.
