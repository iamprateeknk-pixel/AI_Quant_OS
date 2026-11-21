"""Durable source-only candidate test staging on a trusted local filesystem."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re

from ai_quant_os.candidate_test_contracts import CandidateTestRequest
from ai_quant_os.candidate_test_jobs import candidate_test_request
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import encode_metadata, parse_metadata
from ai_quant_os.strategy_prefix_jobs import _chain, _directory, _file_identity

IMAGE_ID = "sha256:5c86773e1c259ac6b89d6ff2bf8ceca386de0013b66c4e3b0cd1f1e91658e5b4"
GUARD_PIN = "59ac6c56aeaf0c062d8f732b77978a11b6163d16fc326c0a3e8ad8014625aa85"
WORKER_PIN = "e653d53494eb12726dc5910de3c0c59eb832d7457c9e5cbcc4cb12eab0ca5a6c"
LOCK_NAME = ".candidate-test-owner.lock"
JOB_NAMES = ("request.json", "runtime_guard.py", "verifier.py")
ROOT_NAMES = {
    LOCK_NAME,
    "job",
    "manifest.json",
    "create-intent.json",
    "create-ack.json",
    "created-inspect.json",
    "start-intent.json",
    "terminal-inspect.json",
    "worker-result.json",
    "remove-intent.json",
    "removed.json",
    "result.json",
}


def persist(path: Path, raw: bytes, mode=0o444):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def persist_metadata(path, value):
    persist(path, encode_metadata(value))


def metadata(path):
    return parse_metadata(read_repair_image_input(path, 65536))


def _repair_worker_assets() -> Path:
    module_path = Path(__file__).resolve()
    source_root = module_path.parents[2]
    source_assets = source_root / "infra" / "repair-worker"
    if (source_root / "pyproject.toml").is_file() and source_assets.is_dir():
        return source_assets
    import sysconfig

    return Path(sysconfig.get_path("data")) / "infra" / "repair-worker"


@dataclass(frozen=True)
class CandidateTestJob:
    path: Path
    manifest: dict
    request: CandidateTestRequest
    manifest_sha256: str

    @property
    def owner(self):
        return self.manifest["owner"]

    @property
    def root(self):
        return self.path.parent


def prepare(package, root, owner):
    if any(row.name != LOCK_NAME for row in root.iterdir()):
        raise ValueError("Candidate test root must be new and empty")
    request = candidate_test_request(package)
    recipes = _repair_worker_assets()
    sources = {
        "request.json": request.model_dump_json().encode(),
        "runtime_guard.py": read_repair_image_input(recipes / "dependency_probe.py", 32768),
        "verifier.py": read_repair_image_input(recipes / "candidate_test_worker.py", 32768),
    }
    if (
        sha256(sources["runtime_guard.py"]).hexdigest() != GUARD_PIN
        or sha256(sources["verifier.py"]).hexdigest() != WORKER_PIN
    ):
        raise ValueError("Trusted candidate worker source pins changed")
    job = root / "job"
    job.mkdir(mode=0o755)
    for name, raw in sources.items():
        persist(job / name, raw)
    manifest = {
        "schema_version": "1.0",
        "purpose": "NO_DATA_CANDIDATE_TEST_JOB",
        "owner": owner,
        "image_id": IMAGE_ID,
        "root": str(root),
        "parents": [[str(path), list(identity)] for path, identity in _chain(root)],
        "job_identity": list(_directory(job)),
        "files": [
            {
                "name": name,
                "sha256": sha256(sources[name]).hexdigest(),
                "identity": list(_file_identity(job / name)),
            }
            for name in JOB_NAMES
        ],
        "request_ref": request.request_ref,
        "package_ref": request.package_ref,
        "candidate_ref": request.candidate_ref,
        "promotion_allowed": False,
    }
    raw = encode_metadata(manifest)
    persist(root / "manifest.json", raw)
    return sha256(raw).hexdigest()


def restore(root, pin):
    if type(pin) is not str or not re.fullmatch(r"[a-f0-9]{64}", pin):
        raise ValueError("Retained independent manifest digest required")
    try:
        raw = read_repair_image_input(root / "manifest.json", 65536)
    except OSError as error:
        raise ValueError("Retained candidate manifest unavailable") from error
    if sha256(raw).hexdigest() != pin:
        raise ValueError("Candidate job manifest digest changed")
    value = parse_metadata(raw)
    if (
        set(value)
        != {
            "schema_version",
            "purpose",
            "owner",
            "image_id",
            "root",
            "parents",
            "job_identity",
            "files",
            "request_ref",
            "package_ref",
            "candidate_ref",
            "promotion_allowed",
        }
        or value["schema_version"] != "1.0"
        or value["purpose"] != "NO_DATA_CANDIDATE_TEST_JOB"
        or value["promotion_allowed"] is not False
        or value["image_id"] != IMAGE_ID
        or type(value["owner"]) is not str
        or not re.fullmatch(r"[a-f0-9]{32}", value["owner"])
        or value["root"] != str(root)
        or encode_metadata(value["parents"])
        != encode_metadata([[str(path), list(identity)] for path, identity in _chain(root)])
        or set(row.name for row in root.iterdir()) - ROOT_NAMES
    ):
        raise ValueError("Candidate job root or manifest binding changed")
    job = root / "job"
    if list(_directory(job)) != value["job_identity"] or sorted(
        row.name for row in job.iterdir()
    ) != list(JOB_NAMES):
        raise ValueError("Candidate test job directory changed")
    if type(value["files"]) is not list or len(value["files"]) != 3:
        raise ValueError("Candidate job source inventory changed")
    sources = {}
    for name, row in zip(JOB_NAMES, value["files"], strict=True):
        if (
            type(row) is not dict
            or set(row) != {"name", "sha256", "identity"}
            or row["name"] != name
        ):
            raise ValueError("Candidate job source binding changed")
        path = job / name
        identity = _file_identity(path)
        if list(identity) != row["identity"] or path.lstat().st_mode & 0o222:
            raise ValueError("Candidate job file native identity changed")
        content = read_repair_image_input(path, 524288 if name == "request.json" else 32768)
        if sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("Candidate job file content changed")
        sources[name] = content
    if (
        sha256(sources["verifier.py"]).hexdigest() != WORKER_PIN
        or sha256(sources["runtime_guard.py"]).hexdigest() != GUARD_PIN
    ):
        raise ValueError("Trusted candidate worker pin changed")
    request = CandidateTestRequest.model_validate_json(sources["request.json"])
    if sources["request.json"] != request.model_dump_json().encode() or (
        request.request_ref,
        request.package_ref,
        request.candidate_ref,
    ) != (value["request_ref"], value["package_ref"], value["candidate_ref"]):
        raise ValueError("Candidate test request differs from manifest")
    return CandidateTestJob(job, value, request, pin)
