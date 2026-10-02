# P10 pinned worker build inputs

## Prepared evidence

The dependency-only worker recipe uses the official Python CPython 3.11.17
Bookworm slim Linux/amd64 image:

`docker.io/library/python@sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5`

The public registry's index, selected manifest and config were fetched without a
daemon. Their bytes are retained in `infra/repair-worker/base_{index,manifest,config}.json`;
`base_image.json` records each digest and the four declared layer digests/sizes.
Git attributes preserve the registry metadata bytes without line-ending
conversion; a real temporary Git index/checkout with `core.autocrlf=true` first
reproduced the manifest digest change and then verified byte preservation.
The scanner's two generic-key findings were the exact public CPython signing-key
fingerprint, present in the config environment and history. A path/rule/exact-match
allowlist covers only that published `GPG_KEY` value, without excluding the file
or other findings. A separate actual Gitleaks staged fixture allowed the two public
occurrences while still detecting an unrelated synthetic API key in the same file.
The index selects exactly one Linux/amd64 manifest, which binds the config with
`PYTHON_VERSION=3.11.17`. These are published metadata hashes, not downloaded layer
verification, a runnable worker image ID, security acceptance or signed provenance.
The historical CPython 3.11.9 base investigated during preparation was not selected.
Digest pinning follows [Docker's guidance](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions);
the Python base recipe is maintained by [Docker Official Images](https://github.com/docker-library/python/blob/master/3.11/slim-bookworm/Dockerfile).

Bounded wheel METADATA dependency closure was independently rechecked for Linux
x86-64 CPython 3.11.17; the earlier 3.11.9 marker selection still yields the same
51 packages. This is not an import or behavior check. Windows host dependencies
were not changed.

## Preparation boundary

`scripts/prepare_repair_image.py` calls `repair_image_inputs` with explicit recipe,
wheel and new output paths. It has no Docker, network, subprocess, import-from-wheel
or archive extraction operation. It validates metadata states, base platform and
hash bindings, recipe base references, unique inventory entries, ordinary
single-link files, wheel size/hash and exact requirements lock agreement. CRLF
and LF lock line endings are accepted; copied bytes retain their original hashes.
JSON duplicate keys and malformed base shapes are rejected as input errors.

Limits: metadata files 1 MiB each, at most 100 inventory entries, selected wheels
16 MiB each and 128 MiB total. Inputs are read and checked before the output is
allocated. Overlapping input/output trees and existing destinations are refused.
Only selected wheel bytes, Dockerfile, requirements.lock and .dockerignore are
copied, with explicit 0755 directory and 0644 file modes. Private files, extra
wheels, project source and candidate/verifier programs are not copied. The local
context has 51 wheels and a final hash inventory receipt; image, execution and
promotion flags remain false. Its receipt is excluded by .dockerignore.

The recipe/metadata roots are trusted operator inputs. Hashes detect differences
from those inputs; no signature or independent trust root is introduced. Recipe
base checks are not a Dockerfile policy analyzer. Protection from hostile host
administrators, directory ancestry/output races, power-loss durability, and a
certificate binding a future completed image are outside this preparation step.
Write failure retains partial output; a final receipt is not proof of fsync,
image installation, native compatibility or runtime isolation. Inspect any partial
directory and prepare a fresh output rather than reusing it automatically.

## Future build and execution gates

The two-stage recipe pins both base stages. Its pip install runs with network
disabled and no index, dependencies, source builds, bytecode compilation or cache;
all wheel hashes are required. The final stage copies installed packages but no
input wheels or project/verifier source. The pinned base's existing toolchain
remains inherited. Network may be needed to retrieve the base; offline installation
does not mean the entire build has already been performed disconnected.

No build/load/push/container start occurred. Docker remains `ENGINE_UNAVAILABLE`;
service/socket repair is untouched. Before execution, remaining gates include
verified layer retrieval and image build, package/security/license review,
actual Linux imports/regressions, immutable worker image ID admission, actual
UID 65534 readability and kernel isolation/resource/deadline probes, reviewed
source-bound verifier/runner, independent baseline/behavior certificates and
failure-safe parent lifecycle. Do not use this base's config digest as a worker
image pin. Temporal repair/shadow/canary/rollback/incident closure remains absent.
The rest of the execution pack is still incomplete; no live/promotion authority
has been enabled.
