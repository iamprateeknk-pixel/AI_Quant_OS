# Application dependency profiles

The application now retains separate hash locks for CPython 3.11 on Windows amd64
and Linux x86_64 with glibc 2.35 or newer. Both include every declared extra
(`dev`, `data`, `trading`, `recovery`, `observability`) and the build requirements for the local
package. Other Python versions and architectures are not covered by these locks.
The isolated repair-image dependency profile remains separate and unchanged.

## Install on Windows

Run from the repository in a new environment, without system packages:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py311.lock
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m pip check
```

On Linux, use `.venv/bin/python` and `requirements/linux-py311.lock`.
The editable application source is installed separately; its integrity is governed
by the reviewed checkout, not the wheel hashes. These files pin dependencies,
not an entire OS, Node installation, frontend artifact, or deployment image.

## Maintain and check

Install the compiler from `requirements/locking-tool.lock` with the same hash and
binary-only options, then run:

```text
python scripts/lock_dependencies.py --check
python scripts/lock_dependencies.py --platform windows
python scripts/lock_dependencies.py --platform linux
```

The compiler must be uv 0.11.29. The bootstrap lock contains the published PyPI
wheel hashes for that release. Resolution uses the public PyPI index, binary
distributions and the explicit target. No source distribution build is allowed.
Existing pins are reused unless changed declarations require resolution changes;
there is no automatic upgrade policy. Review generated changes before accepting
them. Checks reject declaration drift before resolution, then compare complete
resolved bytes without publishing. Failed resolution preserves retained files;
successful regeneration replaces each individual profile atomically. Generating
both profiles is not one transaction: each must be checked before committing.

Dependency identity includes runtime, optional and build requirements, Python
range, target, compiler and binary policy. Application entrypoint edits do not
invalidate it. A check requires network/cache access and does not independently
establish package security or repository provenance.

The `locked-application` CI job checks and installs these profiles, builds the UI
and runs the suite on Windows and Linux. Hosted results remain unverified. Older
specialized jobs still resolve their own extras; this new gate does not imply
every existing job uses the lock.

## Local verification

The fresh Windows environment at `data-local/locked-windows-0055` has system
packages disabled. Hash installation, editable package installation and
`pip check` succeeded. All 96 locked distribution versions match its installed
metadata. A separate hash installation of uv reproduced both profile bytes.
The initial compiler download timed out; the bounded-retry installation succeeded.

Ruff is explicitly pinned to the previously verified 0.15.15. Resolving the earlier
broad range selected 0.16.10, whose expanded default checks reported 700 existing
findings. The established checks and formatting pass under the pinned tool.

Ten focused tests pass, including expected failures observed before implementation
for missing workflow and disabled-runtime-download enforcement. Retained artifact
and genome/protocol CLI checks pass in the new environment without evaluating
replacement sources or promoting agents. The full regression run completed with
**1,035 passed and 15 skipped in 524.75 seconds**, with actual built UI integration
required. The tenth focused test was added after full-suite collection and was
verified separately with all ten dependency tests. The pinned Ruff checks pass
and all 213 Python files satisfy formatting. Service-dependent skips remain;
Linux wheel execution and hosted CI remain unverified.

A fresh Windows index checkout reproduced both lock checks, verifying the LF
attributes rather than relying only on current worktree bytes. The retained
directional artifacts, source closures, genome lineage and protocol exports
remain compatible. The metadata count covers the dependency lock, not pip or the
separately installed editable application. Full build/release phase gates remain
listed in `BUILD_STATUS.md`.
