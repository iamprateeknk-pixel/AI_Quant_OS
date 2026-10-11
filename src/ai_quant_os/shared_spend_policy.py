"""Immutable aggregate role ceilings over a bounded inventory of pinned model policies."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.jev_spend_policy import JevInputSpendPolicy
from ai_quant_os.provider_spend_policy import (
    ProviderSpendPolicy,
    SpendRule,
    decode_spend_fields,
    encode,
)

SHARED_ACCOUNTING = "SHARED_MODEL_COUNTED_TOKENS_AT_DECLARED_RATE_CEILING"


@dataclass(frozen=True)
class SharedSpendPolicy:
    rules: tuple[SpendRule, ...]
    models: tuple[ProviderSpendPolicy, ...]

    def __post_init__(self) -> None:
        if (
            type(self.rules) is not tuple
            or len(self.rules) != len(ROLE_CEILINGS)
            or any(type(row) is not SpendRule for row in self.rules)
            or {row.agent_id for row in self.rules} != set(ROLE_CEILINGS)
            or type(self.models) is not tuple
            or not 1 <= len(self.models) <= 8
            or any(
                type(row) not in (ProviderSpendPolicy, JevInputSpendPolicy) for row in self.models
            )
            or len({row.model for row in self.models}) != len(self.models)
        ):
            raise ValueError("Complete aggregate roles and unique reviewed model policies required")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda row: row.agent_id)))
        object.__setattr__(self, "models", tuple(sorted(self.models, key=lambda row: row.model)))
        if len(self.encode()) > 65536:
            raise ValueError("Shared spending policy exceeds capacity")

    @property
    def model(self) -> None:
        return None

    @property
    def accounting(self) -> str:
        return SHARED_ACCOUNTING

    def contract(self, model: str) -> ProviderSpendPolicy:
        if type(model) is not str:
            raise ValueError("Pinned spending model required")
        selected = next((row for row in self.models if row.model == model), None)
        if selected is None:
            raise ValueError("Model is outside the original shared spending policy")
        return selected

    def encode(self) -> bytes:
        return encode(
            {
                "schema_version": "1.0",
                "accounting": self.accounting,
                "rules": [asdict(row) for row in self.rules],
                "models": [json.loads(row.encode()) for row in self.models],
            }
        )

    @property
    def fingerprint(self) -> str:
        return sha256(self.encode()).hexdigest()

    @classmethod
    def decode(cls, raw: bytes) -> "SharedSpendPolicy":
        value = decode_spend_fields(raw)
        if (
            value.pop("accounting", None) != SHARED_ACCOUNTING
            or type(value.get("models")) is not list
            or not 1 <= len(value["models"]) <= 8
            or any(
                type(row) is not dict or row.get("accounting") == SHARED_ACCOUNTING
                for row in value["models"]
            )
        ):
            raise ValueError("Bounded shared model accounting required")
        try:
            value["models"] = tuple(decode_spending_policy(encode(row)) for row in value["models"])
            return cls(**value)
        except (TypeError, KeyError):
            raise ValueError("Invalid shared spending policy fields") from None


def decode_spending_policy(raw: bytes) -> ProviderSpendPolicy | SharedSpendPolicy:
    # Parse bounded unique fields before dispatch; unknown types never fall through.
    value = decode_spend_fields(raw)
    accounting = value.get("accounting")
    if accounting == SHARED_ACCOUNTING:
        return SharedSpendPolicy.decode(raw)
    if "accounting" in value:
        return JevInputSpendPolicy.decode(raw)
    return ProviderSpendPolicy.decode(raw)
