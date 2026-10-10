"""Pinned Jev 1.13 input-only admission; no tokenizer or output-cap assumption."""

from dataclasses import asdict, dataclass

from ai_quant_os.agent_permissions import ROLE_CEILINGS
from ai_quant_os.provider_spend_policy import (
    ProviderSpendPolicy,
    SpendRule,
    bounded_integer,
    decode_spend_fields,
    encode,
)

# The published 64k whole-request ceiling is rounded upward, not inferred from bytes.
# Other model versions require a separately reviewed bound and billing contract.
JEV_INPUT_CEILING = 65536
JEV_ACCOUNTING = "JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"


@dataclass(frozen=True)
class JevInputSpendPolicy(ProviderSpendPolicy):
    def __post_init__(self) -> None:
        if (
            type(self.model) is not str
            or self.model != "jev-1.13.0"
            or not bounded_integer(self.input_nano_usd)
            or self.input_nano_usd < 42
            or type(self.output_nano_usd) is not int
            or self.output_nano_usd != 0
            or type(self.rate_reference) is not str
            or not 1 <= len(self.rate_reference) <= 256
            or any(ord(char) < 32 for char in self.rate_reference)
            or type(self.rules) is not tuple
            or len(self.rules) != len(ROLE_CEILINGS)
            or any(type(row) is not SpendRule for row in self.rules)
            or {row.agent_id for row in self.rules} != set(ROLE_CEILINGS)
        ):
            raise ValueError(
                "Reviewed Jev contract, input rate ceiling and all role limits required"
            )
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda row: row.agent_id)))

    @property
    def accounting(self) -> str:
        return JEV_ACCOUNTING

    def valid_reservation(self, inputs: object, outputs: object) -> bool:
        return (
            type(inputs) is int
            and inputs == JEV_INPUT_CEILING
            and type(outputs) is int
            and outputs == 0
        )

    def tokens(self, inputs: int, outputs: int) -> int:
        return inputs

    def exceeded(self, reserved: dict, reported: dict) -> bool:
        return reported["input_tokens"] > reserved["input_tokens"]

    def encode(self) -> bytes:
        return encode(
            {
                "schema_version": "1.0",
                "accounting": self.accounting,
                "input_reservation_ceiling": JEV_INPUT_CEILING,
                **asdict(self),
            }
        )

    @classmethod
    def decode(cls, raw: bytes) -> "JevInputSpendPolicy":
        value = decode_spend_fields(raw)
        ceiling = value.pop("input_reservation_ceiling", None)
        if (
            value.pop("accounting", None) != JEV_ACCOUNTING
            or type(ceiling) is not int
            or ceiling != JEV_INPUT_CEILING
        ):
            raise ValueError("Jev input accounting required")
        try:
            return cls(**value)
        except (TypeError, KeyError):
            raise ValueError("Invalid Jev spending policy fields") from None
