"""Bounded original file prefixes, not a mutable ledger or collector health lease."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from time import monotonic

from ai_quant_os.collector_trace import select_spans

MAX_FILE = 32 * 1024 * 1024
MAX_LINE = 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous JSON keys")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Finite JSON required")


class CollectorTraceSource:
    def __init__(self, path: Path | None):
        self.path = Path(path).absolute() if path is not None else None

    def _paths(self, deadline):
        pattern = re.compile(
            re.escape(self.path.stem)
            + r"-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{3}"
            + re.escape(self.path.suffix)
        )
        paths = []
        for index, path in enumerate(self.path.parent.iterdir()):
            if index >= 1024 or monotonic() > deadline:
                raise ValueError("Collector directory scan bound reached")
            if path.name == self.path.name or pattern.fullmatch(path.name):
                paths.append(path)
                if len(paths) > 4:
                    raise ValueError("Collector retention inventory differs")
        if self.path not in paths:
            raise ValueError("Original active collector file absent")
        return sorted(paths)

    def read(self, trace_id):
        if str(self.path).startswith("\\\\"):
            raise ValueError("Local collector file required")
        deadline = monotonic() + 2.5
        paths = self._paths(deadline)
        spans, files = [], []
        for ordinal, path in enumerate(paths):
            if path.is_symlink():
                raise ValueError("Original regular file required")
            with path.open("rb") as stream:
                initial = os.fstat(stream.fileno())
                if not stat.S_ISREG(initial.st_mode) or not 0 <= initial.st_size <= MAX_FILE:
                    raise ValueError("Bounded regular prefix required")
                remaining = initial.st_size
                digest = sha256()
                while remaining:
                    if monotonic() > deadline:
                        raise ValueError("Collector read deadline reached")
                    line = stream.readline(min(remaining, MAX_LINE + 1))
                    if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                        raise ValueError("Complete bounded original record required")
                    remaining -= len(line)
                    digest.update(line)
                    document = json.loads(
                        line,
                        object_pairs_hook=unique_object,
                        parse_constant=reject_constant,
                    )
                    spans.extend(select_spans(document, trace_id))
                    if len(spans) > 128:
                        raise ValueError("Selected trace exceeds span bound")
                stream.seek(0)
                remaining = initial.st_size
                verified = sha256()
                while remaining:
                    if monotonic() > deadline:
                        raise ValueError("Collector prefix check deadline reached")
                    chunk = stream.read(min(remaining, MAX_LINE))
                    if not chunk:
                        raise ValueError("Original prefix shortened")
                    remaining -= len(chunk)
                    verified.update(chunk)
                current = path.stat()
                if (
                    digest.digest() != verified.digest()
                    or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
                    or current.st_size < initial.st_size
                ):
                    raise ValueError("Original collector prefix changed")
                files.append(
                    {
                        "ordinal": ordinal,
                        "prefix_bytes": initial.st_size,
                        "prefix_sha256": digest.hexdigest(),
                    }
                )
        if paths != self._paths(deadline):
            raise ValueError("Collector inventory changed")
        by_id = {span.span_id: span for span in spans}
        if len(by_id) != len(spans):
            raise ValueError("Ambiguous duplicate span IDs")
        for span in spans:
            visited, current = set(), span
            while current is not None:
                if current.span_id in visited:
                    raise ValueError("Cyclic source parentage")
                visited.add(current.span_id)
                current = by_id.get(current.parent_span_id)
        return tuple(sorted(spans, key=lambda span: (int(span.start_ns), span.span_id))), tuple(
            files
        )
