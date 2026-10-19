# Signed repair verification certificates

## Contract

`ai_quant_os.repair_certificates.verify_repair_certificate` validates a bounded,
canonical Ed25519-signed certificate from an independently operated verifier.
The certificate pins the candidate commit and complete source snapshot, rollback
commit, original incident, proposed agent/version, archived replay, verifier
source, test suite and container image. It contains a unique ordered test result
list with evidence hashes. A passing result is accepted only when every listed
test passes; a signed failure remains `REPAIR_FAILED_AWAITING_REVIEW`.

The verifier never accepts a signature key from the certificate itself. The new
`load_repair_verifier_trust_store` loader accepts a canonical signer registry
only when it has a valid Ed25519 signature from a separately provisioned root
public key. Each registry entry binds a reviewer ID, reviewer key and approved
verifier-source hash; duplicate or unsorted identities are rejected. The
registry format is documented in
[`repair-verifier-trust-store.schema.json`](schemas/repair-verifier-trust-store.schema.json).
The caller still must supply the expected replay, suite, verifier-source and image
pins from server-owned evidence. Root-key rotation and registry publication need
a separately reviewed configuration process; the trust root is not bundled with
candidate source. The signature attests who issued the certificate and what bytes
they signed; it does not prove that the verifier's logic is correct or that its
tests are complete. Independent review and live isolation acceptance of the
verifier remain required.

The result is evidence only. `promotion_allowed` is fixed false; validation does
not release quarantine, close an incident or otherwise change risk state, deploy
a candidate or authorize shadow/canary execution. Quote incident acknowledgement
is available separately after a fresh trusted quote. The optional PAPER Temporal API now loads
the root-signed registry at startup, accepts operator-authenticated certificate
submissions up to 64 KiB, stores them by content hash, and signals the exact
workflow. The verification activity rechecks the hash and certificate against
the retained candidate, successful execution receipt, request/replay, suite and
image pins. The one-shot runner retains the exact request bytes beside its
execution receipt so this binding remains available after the temporary job is
cleaned. Certificate publication is atomic and each workflow immutably binds to
its first certificate hash: exact retries are idempotent and competing
certificates are rejected. A signed PASS ends at
`INDEPENDENT_VERIFICATION_PASSED`; a signed FAIL
ends at review. Invalid or unbound submissions fail closed to `REVIEW_REQUIRED`.
The workflow does not release quarantine or start shadow/canary work. Quote
incident acknowledgement is now available separately after a fresh trusted
quote; agent and critical incident resolution remain gated. Shadow/canary,
independent verifier review and rollback automation remain open.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_repair_certificates.py
.venv/Scripts/python.exe -m pytest -q tests/test_temporal_repair.py tests/test_repair_temporal_api.py
.venv/Scripts/python.exe -m ruff check src/ai_quant_os/repair_certificates.py tests/test_repair_certificates.py
.venv/Scripts/python.exe scripts/lock_dependencies.py --check
```

Tests use ephemeral Ed25519 keys and real signatures. They check exact source and
replay binding, untrusted signer refusal, signature tampering, duplicate/unknown
fields, test/verdict consistency, version ordering, schema validity and false
promotion authority. No private key or repair candidate is retained.

The regression suite also covers the separately root-signed trust registry, its reviewer-source pins, duplicate signer rejection, wrong-root rejection and tamper detection. The registry schema is checked against a real root-signed fixture. A real Ed25519-signed PASS now traverses the authenticated API, durable content-addressed storage, Temporal signal and verification activity against retained execution, request/replay and candidate evidence.
