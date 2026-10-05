# Controlled Linux worker enforcement probes

Five fixed adversarial scenarios exercise the existing worker profile on the real
local Linux engine. They accept only a bounded scenario selector; proposed code,
SDK sources, datasets, commands, paths and configurable limits are not accepted.
The original image, shared guard and production candidate/strategy controllers
remain unchanged.

## Observed behavior

| Scenario | Required observation |
| --- | --- |
| Privilege and scratch execution | Setting UID/GID to zero, changing supplementary groups and creating an IPv4 raw socket all return `EPERM`; directly executing an executable script in scratch returns `EACCES`. |
| Memory | Touching a fixed 1 GiB allocation exceeds the existing 512 MiB memory ceiling; the exact owned container exits 137 with `OOMKilled=true`. |
| CPU | A fixed busy loop is killed under the existing 30-second CPU limit; the owned container exits 137 with `OOMKilled=false`. |
| Processes | Fixed child creation is denied with `EAGAIN` before exceeding 63 children/64 observed processes. All controlled children are reaped before normal exit. |
| Files | Writing beyond exactly 1 MiB returns `EFBIG`; opening descriptors exhausts the existing 128-descriptor ceiling with `EMFILE`. All probe descriptors close before normal exit. |

The file probe temporarily ignores `SIGXFSZ` to observe its write error directly;
this does not change `RLIMIT_FSIZE`, and the original handler is restored. Process
denial may be caused by the cgroup ceiling or UID-wide `RLIMIT_NPROC`; the probe
does not claim to distinguish them. CPU enforcement is an observed local kill,
not external cryptographic kernel attestation. Resource probes deliberately cause
only their own isolated container to reach the fixed limit.

## Run

Use the verified import procedure in REPAIR_IMAGE_LOADING_HANDOFF.md first. The
exact local dependency image is required:
`sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4`.
No pull or image substitution is performed. Choose a new evidence directory;
pytest may clear an existing base directory.

```powershell
$env:AI_QUANT_ENFORCEMENT_ENGINE_TEST = '1'
data-local/locked-windows-0055/Scripts/python.exe -m pytest -q tests/test_worker_enforcement_probe.py --basetemp=data-local/worker-enforcement-another-new-run
Remove-Item Env:AI_QUANT_ENFORCEMENT_ENGINE_TEST
```

The opt-in harness stages exactly trusted probe/guard/request files and records
source hashes and the fixed create recipe before one create call. The shared
three-file runtime guard runs before any adversarial operation. The parent admits
created metadata with the common confinement predicate, records a start intent,
uses the bounded 45-second local transport, and checks exact owned terminal state.
It retains observations and removal evidence, including for expected CPU/OOM
kills. Cleanup re-inspects owner/name/image and removes only the exact immutable
container ID. Inputs are checked before and after the operation.

The main retained evidence is in `data-local/worker-enforcement-0082-complete`;
the tracked digest/observation summary is
`docs/evaluations/worker-enforcement-0082.json`. A cold staged checkout is verified
separately. Without the explicit gate, five real-engine cases skip; request and
non-Linux host-refusal checks still run.

## Scope

This is a controlled development test harness, not a production orphan recovery
service. Parent death or ambiguous create/start/removal retains original records
and requires investigation; unknown absence is not evidence of acknowledged
removal. Never reuse retained evidence paths or delete intents to retry.

The successful probes strengthen observed kernel-enforcement evidence. They do
not establish independent daemon/source attestation, resistance to arbitrary
malicious candidate behavior, holdout separation from generation context,
statistical acceptance, complete A10 mission behavior, champion promotion or
production/live readiness. `isolation_verified` and `promotion_allowed` remain
false. Existing candidate/strategy job authority fields are unchanged.
