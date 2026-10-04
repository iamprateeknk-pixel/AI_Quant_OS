"""Stdlib-only prefix worker. Candidate import is reachable only after runtime checks.

This is a defense-in-depth guard, not external Docker admission or attestation.
Do not execute proposed source on the parent host. The parent must independently
admit the immutable image/container and bind the exact single request before start.
"""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, localcontext
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SDK_PATHS = (
    "ai_quant_os/__init__.py",
    "ai_quant_os/contracts.py",
    "ai_quant_os/market_data.py",
    "ai_quant_os/strategy_contracts.py",
)
ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/scratch/home",
    "TMPDIR": "/scratch",
}
LIMITS = {"cpu": (30, 30), "fsize": (1048576, 1048576), "nofile": (128, 128), "nproc": (64, 64)}
STATUS = {
    "NoNewPrivs": 1,
    "Seccomp": 2,
    "CapInh": 0,
    "CapPrm": 0,
    "CapEff": 0,
    "CapBnd": 0,
    "CapAmb": 0,
}
CGROUP = {
    "memory.max": "536870912",
    "memory.swap.max": "0",
    "pids.max": "64",
    "cpu.max": "100000 100000",
}


@dataclass(frozen=True)
class HistoryBar:
    instrument_id: str
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class PrefixRequest:
    invocation_ref: str
    source: bytes
    sdk_files: tuple[tuple[str, bytes], ...]
    history: tuple[HistoryBar, ...]


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("Worker object inventory mismatch")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate worker JSON key")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("Non-finite worker JSON constant")


def _digest(value, prefix=""):
    if type(value) is not str or not re.fullmatch(re.escape(prefix) + "[a-f0-9]{64}", value):
        raise ValueError("Invalid worker digest identity")


def _clock(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError("Invalid worker clock")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Worker clock must be UTC")
    return result.astimezone(timezone.utc)


def _decimal(value):
    if type(value) is not str or not 0 < len(value) <= 4096:
        raise ValueError("Invalid worker decimal wire")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Non-finite worker decimal")
    return result


def _bar(value, instrument):
    _object(value, ("instrument_id", "observed_at", "open", "high", "low", "close", "volume"))
    if value["instrument_id"] != instrument:
        raise ValueError("Worker history instrument mismatch")
    prices = tuple(_decimal(value[name]) for name in ("open", "high", "low", "close"))
    volume = _decimal(value["volume"])
    opening, high, low, close = prices
    if (
        min(prices) <= 0
        or high < max(opening, low, close)
        or low > min(opening, high, close)
        or volume < 0
    ):
        raise ValueError("Invalid worker OHLCV")
    return HistoryBar(instrument, _clock(value["observed_at"]), *prices, volume)


def _source(value):
    _object(value, ("path", "text", "sha256"))
    if (
        value["path"] != "candidate.py"
        or type(value["text"]) is not str
        or not 0 < len(value["text"]) <= 8192
        or "\r" in value["text"]
    ):
        raise ValueError("Invalid worker candidate inventory")
    raw = value["text"].encode()
    _digest(value["sha256"])
    if sha256(raw).hexdigest() != value["sha256"]:
        raise ValueError("Worker candidate byte mismatch")
    return raw


def _sdk(values):
    if type(values) is not list or len(values) != 4:
        raise ValueError("Invalid worker SDK inventory")
    result = []
    for value, path in zip(values, SDK_PATHS, strict=True):
        _object(value, ("path", "raw_base64", "sha256"))
        if (
            value["path"] != path
            or type(value["raw_base64"]) is not str
            or len(value["raw_base64"]) > 87384
        ):
            raise ValueError("Invalid worker SDK file")
        _digest(value["sha256"])
        raw = base64.b64decode(value["raw_base64"], validate=True)
        if (
            not 0 < len(raw) <= 65536
            or base64.b64encode(raw).decode() != value["raw_base64"]
            or sha256(raw).hexdigest() != value["sha256"]
        ):
            raise ValueError("Worker SDK byte mismatch")
        raw.decode("utf-8")
        result.append((path, raw))
    return tuple(result)


def decode_request(raw: bytes) -> PrefixRequest:
    """Validate inert canonical input only; never compile, import or materialize source."""
    if type(raw) is not bytes or not 0 < len(raw) <= 524288:
        raise ValueError("Worker input byte bound/type mismatch")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_no_constant)
        _object(
            payload,
            (
                "schema_version",
                "package_ref",
                "candidate_ref",
                "index",
                "execute_at",
                "instrument_id",
                "source",
                "sdk_files",
                "history",
                "history_sha256",
                "status",
                "isolation_verified",
                "promotion_authorized",
            ),
        )
        if json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode() != raw:
            raise ValueError("Worker input must retain canonical parent wire bytes")
        if (
            payload["schema_version"] != "1.0"
            or payload["status"] != "WAIT_FOR_CONFINED_PREFIX_WORKER"
            or payload["isolation_verified"] is not False
            or payload["promotion_authorized"] is not False
        ):
            raise ValueError("Worker input cannot grant authority")
        _digest(payload["package_ref"], "validation-package:")
        _digest(payload["candidate_ref"], "strategy-candidate:")
        _digest(payload["history_sha256"])
        if (
            type(payload["index"]) is not int
            or not 2 <= payload["index"] <= 4095
            or payload["instrument_id"] not in ("EURUSD", "GBPUSD", "USDJPY", "EURJPY")
        ):
            raise ValueError("Invalid worker index/instrument")
        if type(payload["history"]) is not list or len(payload["history"]) != 2:
            raise ValueError("Worker requires exactly two prior closed bars")
        history = tuple(_bar(value, payload["instrument_id"]) for value in payload["history"])
        if not history[0].observed_at < history[1].observed_at < _clock(payload["execute_at"]):
            raise ValueError("Worker history is future or nonchronological")
        with localcontext(Context(prec=28)):
            records = [
                [
                    row.instrument_id,
                    row.observed_at.isoformat(),
                    *(
                        str(getattr(row, field).normalize())
                        for field in ("open", "high", "low", "close", "volume")
                    ),
                ]
                for row in history
            ]
            fingerprint = sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
        if fingerprint != payload["history_sha256"]:
            raise ValueError("Worker history fingerprint mismatch")
        return PrefixRequest(
            "candidate-invocation:" + sha256(raw).hexdigest(),
            _source(payload["source"]),
            _sdk(payload["sdk_files"]),
            history,
        )
    except (UnicodeError, RecursionError, DecimalException) as error:
        raise ValueError("Invalid worker input encoding or arithmetic") from error


def validate_runtime_profile(evidence: dict) -> None:
    """Check observed prerequisites; not an independently trusted isolation certificate."""
    required = {
        "platform": "linux",
        "implementation": "cpython",
        "version": (3, 11, 17),
        "isolated": 1,
        "no_bytecode": 1,
        "uid": 65534,
        "gid": 65534,
        "working_directory": "/scratch",
        "worker_path": "/opt/strategy/worker.py",
        "cgroup": CGROUP,
        "network_interfaces": ("lo",),
        "job_files": ("request.json",),
        "scratch_files": (),
    }
    _object(evidence, set(required) | {"groups", "environment", "status", "limits", "mounts"})
    for key, expected in required.items():
        actual = evidence.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError("Worker runtime prerequisite mismatch")
    if type(evidence.get("groups")) is not tuple or any(
        type(group) is not int or group != 65534 for group in evidence["groups"]
    ):
        raise ValueError("Worker supplementary group mismatch")
    environment = evidence["environment"]
    if (
        type(environment) is not dict
        or {key: value for key, value in environment.items() if key != "LC_CTYPE"} != ENVIRONMENT
        or environment.get("LC_CTYPE", "C.UTF-8") != "C.UTF-8"
    ):
        raise ValueError("Worker environment mismatch")
    _object(evidence["status"], STATUS)
    if any(
        type(evidence["status"][key]) is not int or evidence["status"][key] != expected
        for key, expected in STATUS.items()
    ):
        raise ValueError("Worker privilege/filter mismatch")
    limits = evidence["limits"]
    _object(limits, LIMITS)
    if limits != LIMITS or any(
        type(value) is not tuple or any(type(part) is not int for part in value)
        for value in limits.values()
    ):
        raise ValueError("Worker resource limits mismatch")
    mounts = evidence["mounts"]
    _object(mounts, ("/", "/job", "/scratch"))
    for path in ("/", "/job"):
        if type(mounts[path]) is not dict or type(mounts[path].get("options")) is not set:
            raise ValueError("Invalid worker mount metadata")
        if "ro" not in mounts[path]["options"] or "rw" in mounts[path]["options"]:
            raise ValueError("Worker root/job must be read-only")
    scratch = mounts["/scratch"]
    _object(scratch, ("options", "filesystem", "size"))
    if type(scratch["options"]) is not set:
        raise ValueError("Invalid worker scratch options")
    if (
        not {"rw", "noexec", "nosuid", "nodev"}.issubset(scratch["options"])
        or "ro" in scratch["options"]
        or scratch["filesystem"] != "tmpfs"
        or type(scratch["size"]) is not int
        or scratch["size"] != 67108864
    ):
        raise ValueError("Worker scratch confinement mismatch")


def _runtime_profile():
    if sys.platform != "linux" or os.geteuid() != 65534:
        raise ValueError("Worker requires the admitted Linux non-root runtime")
    import resource

    status = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in STATUS:
            status[key] = int(value.strip(), 16 if key.startswith("Cap") else 10)
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields, after = line.split(" - ", 1)
        columns = fields.split()
        path = columns[4]
        if path in ("/", "/job", "/scratch"):
            if path in mounts:
                raise ValueError("Duplicate worker mount")
            mounts[path] = {"options": set(columns[5].split(",")), "filesystem": after.split()[0]}
    scratch = os.statvfs("/scratch")
    mounts["/scratch"]["size"] = scratch.f_blocks * scratch.f_frsize
    return {
        "platform": sys.platform,
        "implementation": sys.implementation.name,
        "version": tuple(sys.version_info[:3]),
        "isolated": sys.flags.isolated,
        "no_bytecode": sys.flags.dont_write_bytecode,
        "uid": os.geteuid(),
        "gid": os.getegid(),
        "groups": tuple(os.getgroups()),
        "working_directory": os.getcwd(),
        "worker_path": str(Path(__file__).resolve()),
        "environment": dict(os.environ),
        "status": status,
        "cgroup": {
            name: " ".join((Path("/sys/fs/cgroup") / name).read_text().split()) for name in CGROUP
        },
        "mounts": mounts,
        "network_interfaces": tuple(sorted(row.name for row in Path("/sys/class/net").iterdir())),
        "limits": {
            name: resource.getrlimit(getattr(resource, "RLIMIT_" + name.upper())) for name in LIMITS
        },
        "job_files": tuple(sorted(row.name for row in Path("/job").iterdir())),
        "scratch_files": tuple(sorted(row.name for row in Path("/scratch").iterdir())),
    }


def encode_target(invocation_ref: str, target: int) -> bytes:
    _digest(invocation_ref, "candidate-invocation:")
    if type(target) is not int or target not in (-1, 0, 1):
        raise ValueError("Worker target must be a strict unit integer")
    return json.dumps(
        {
            "schema_version": "1.0",
            "invocation_ref": invocation_ref,
            "target": target,
            "status": "UNATTESTED_TARGET_OUTPUT",
            "isolation_verified": False,
            "promotion_authorized": False,
        },
        separators=(",", ":"),
    ).encode()


def _read_request():
    descriptor = os.open("/job/request.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 524288
        ):
            raise ValueError("Worker request must be a bounded single-link regular file")
        raw = source.read(524289)
    if len(raw) != metadata.st_size:
        raise ValueError("Worker request changed during read")
    return decode_request(raw)


def _run_inside_runtime():
    # Guard before input access, source materialization or any proposed module import.
    validate_runtime_profile(_runtime_profile())
    request = _read_request()
    runtime = Path("/scratch/runtime")
    runtime.mkdir(mode=0o700)
    Path("/scratch/home").mkdir(mode=0o700)
    for path, raw in (*request.sdk_files, ("candidate.py", request.source)):
        destination = runtime / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as source:
            source.write(raw)
    if "candidate" in sys.modules or "ai_quant_os" in sys.modules:
        raise ValueError("Worker requires fresh proposed module state")
    sys.path.insert(0, str(runtime))
    # Only this guarded, future-container path imports the retained SDK/candidate.
    sdk = importlib.import_module("ai_quant_os.market_data")
    candidate = importlib.import_module("candidate")
    history = tuple(sdk.Bar(**vars(row)) for row in request.history)
    with localcontext(Context(prec=28)):
        target = candidate.HypothesisDirection().target_position(history)
    return encode_target(request.invocation_ref, target)


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ValueError("Worker paths and arguments are fixed")
        raw = _run_inside_runtime()
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # One untrusted-process failure boundary. Never echo request/source/exception text.
        sys.stderr.write("PREFIX_WORKER_REFUSED\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
