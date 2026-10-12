import json
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.encoders import jsonable_encoder
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from ai_quant_os.paper_instruments import PAPER_INSTRUMENTS
from ai_quant_os.world_state import CurrencyTwin, InstrumentWorldState, WorldState, WorldStateEngine


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = "urn:ai-quant-os:contract:paper-world-state:1.0"


def world_state_wire() -> dict[str, object]:
    now = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    snapshot = WorldStateEngine(PAPER_INSTRUMENTS).snapshot((), now=now)
    return {
        "mode": "PAPER",
        **jsonable_encoder(snapshot, custom_encoder={Decimal: str}),
    }


def test_paper_world_state_v1_schema_matches_runtime_and_actual_api_projection():
    schema = json.loads(
        (ROOT / "docs/schemas/paper-world-state-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == SCHEMA_ID
    assert set(schema["properties"]) == {field.name for field in fields(WorldState)} | {"mode"}
    assert set(schema["$defs"]["InstrumentWorldState"]["properties"]) == {
        field.name for field in fields(InstrumentWorldState)
    }
    assert set(schema["$defs"]["CurrencyTwin"]["properties"]) == {
        field.name for field in fields(CurrencyTwin)
    }

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    payload = world_state_wire()
    validator.validate(payload)
    with pytest.raises(ValidationError):
        validator.validate({**payload, "unrecognized": True})
    with pytest.raises(ValidationError):
        validator.validate({**payload, "as_of": "2026-10-13T10:00:00"})
