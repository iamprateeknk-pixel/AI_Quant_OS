"""Selected collector span values; raw local file contents remain untrusted."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRACE_PATTERN = r"^[a-f0-9]{32}$"
SPAN_PATTERN = r"^[a-f0-9]{16}$"
NAMES = {
    "paper.http",
    "paper.market.snapshot",
    "paper.risk.evaluate",
    "paper.order.submit",
    "paper.order.replay",
    "paper.order.recover",
    "paper.agent.classify",
    "paper.agent.review",
    "paper.agent.archive",
    "paper.provider.classify",
    "paper.provider.review",
}


class CollectorSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    trace_id: str = Field(pattern=TRACE_PATTERN)
    span_id: str = Field(pattern=SPAN_PATTERN)
    parent_span_id: str | None = Field(default=None, pattern=SPAN_PATTERN)
    name: str
    start_ns: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    end_ns: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    status: Literal["UNSET", "OK", "ERROR"]
    http_method: Literal["GET", "POST", "DELETE", "PUT", "PATCH", "OTHER"] | None = None
    http_route: str | None = Field(
        default=None, pattern=r"^(UNMATCHED|/api/v1/[A-Za-z0-9_/{}/.-]{1,240})$"
    )
    http_status: int | None = Field(default=None, strict=True, ge=100, le=599)
    risk_decision: Literal["APPROVE", "REJECT", "HALT"] | None = None
    order_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$"
    )
    order_status: (
        Literal[
            "PENDING",
            "DISPATCHING",
            "ACCEPTED",
            "REJECTED",
            "UNKNOWN",
            "FILLED",
            "PARTIALLY_FILLED",
        ]
        | None
    ) = None
    envelope_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$"
    )
    parent_envelope_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$"
    )
    agent_route: Literal["DEEP_REVIEW", "WAIT_FOR_DATA"] | None = None
    review_status: (
        Literal[
            "COMPLETED", "NOT_CONFIGURED", "WAIT_FOR_DATA", "BUSY", "TIMEOUT", "FAILED", "INVALID"
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def original_shape(self):
        if self.name not in NAMES or any(
            int(value, 16) == 0
            for value in [self.trace_id, self.span_id]
            + ([self.parent_span_id] if self.parent_span_id else [])
        ):
            raise ValueError("Selected original SDK shape required")
        if (
            self.parent_span_id == self.span_id
            or not int(self.start_ns) <= int(self.end_ns) <= 9223372036854775807
        ):
            raise ValueError("Selected original SDK ordering required")
        return self


def attributes(rows):
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError("Bounded attributes required")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("key"), str)
            or row["key"] in result
            or not isinstance(row.get("value"), dict)
        ):
            raise ValueError("Unique typed attributes required")
        result[row["key"]] = row["value"]
    return result


def select_spans(document, trace_id):
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        raise ValueError("Original OTLP JSON records required")
    selected = []
    for resource in document["resourceSpans"]:
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                if span.get("traceId") != trace_id:
                    continue
                source = attributes(resource["resource"]["attributes"])
                if (
                    source.get("service.name") != {"stringValue": "ai-quant-paper"}
                    or source.get("service.version") != {"stringValue": "0.1.0"}
                    or source.get("ai_quant.mode") != {"stringValue": "PAPER"}
                    or scope.get("scope") != {"name": "ai_quant_os.paper", "version": "1.0"}
                ):
                    raise ValueError("Original paper instrumentation source required")
                values = attributes(span.get("attributes", []))
                if values.get("ai_quant.mode") != {"stringValue": "PAPER"}:
                    raise ValueError("Original PAPER span required")
                status = span.get("status", {}).get("code", 0)
                if type(status) is not int or status not in (0, 1, 2):
                    raise ValueError("Original SDK status required")
                output = {
                    "trace_id": trace_id,
                    "span_id": span["spanId"],
                    "parent_span_id": span.get("parentSpanId") or None,
                    "name": span["name"],
                    "start_ns": span["startTimeUnixNano"],
                    "end_ns": span["endTimeUnixNano"],
                    "status": {0: "UNSET", 1: "OK", 2: "ERROR"}[status],
                }
                keys = {
                    "http.request.method": "http_method",
                    "http.route": "http_route",
                    "ai_quant.risk.decision": "risk_decision",
                    "ai_quant.order.id": "order_id",
                    "ai_quant.order.status": "order_status",
                    "ai_quant.agent.envelope_id": "envelope_id",
                    "ai_quant.agent.parent_envelope_id": "parent_envelope_id",
                    "ai_quant.agent.route": "agent_route",
                    "ai_quant.review.status": "review_status",
                }
                for key, field in keys.items():
                    if key in values:
                        value = values[key]
                        if set(value) != {"stringValue"} or not isinstance(
                            value["stringValue"], str
                        ):
                            raise ValueError("Selected string attribute required")
                        output[field] = value["stringValue"]
                if "http.response.status_code" in values:
                    value = values["http.response.status_code"]
                    if (
                        set(value) != {"intValue"}
                        or not isinstance(value["intValue"], str)
                        or not re.fullmatch(r"[1-5][0-9]{2}", value["intValue"])
                    ):
                        raise ValueError("Selected HTTP status required")
                    output["http_status"] = int(value["intValue"])
                selected.append(CollectorSpan.model_validate(output))
    return selected
