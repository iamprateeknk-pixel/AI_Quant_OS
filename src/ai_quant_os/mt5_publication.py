"""Bounded atomic JSON files for local demo service publications."""

import json
import os
import tempfile
from pathlib import Path

PUBLICATION_BYTES = 16384


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate publication field")
        result[key] = value
    return result


def read_publication(path: Path, model):
    with path.open("rb") as stream:
        raw = stream.read(PUBLICATION_BYTES + 1)
    if len(raw) > PUBLICATION_BYTES:
        raise ValueError("Publication bound exceeded")
    return model.model_validate(json.loads(raw, object_pairs_hook=unique_fields))


def write_publication(path: Path, record):
    payload = record.model_dump_json().encode("utf-8")
    if len(payload) > PUBLICATION_BYTES:
        raise ValueError("Publication bound exceeded")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="demo-health-", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
