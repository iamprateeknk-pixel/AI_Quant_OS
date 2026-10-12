"""Selected current local native rule observations, without notification/control authority."""

import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SPEC = {
    "PaperHttpScrapeFailed": (
        'up{job="paper_http"} == 0',
        "Paper HTTP metrics scrape failed",
        "docs/runbooks/paper-http-monitoring.md#scrape-failed",
    ),
    "PaperHttpScrapeMissing": (
        'absent(up{job="paper_http"})',
        "Paper HTTP metrics scrape observation missing",
        "docs/runbooks/paper-http-monitoring.md#scrape-missing",
    ),
}


class MonitoringRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["PaperHttpScrapeFailed", "PaperHttpScrapeMissing"]
    state: Literal["INACTIVE", "PENDING", "FIRING"]
    evaluated_at: AwareDatetime
    active_since: AwareDatetime | None
    hold_seconds: Literal[120] = 120
    severity: Literal["warning"] = "warning"
    runbook_path: str

    @model_validator(mode="after")
    def coherent(self):
        if (
            self.runbook_path != SPEC[self.name][2]
            or self.evaluated_at.utcoffset().total_seconds() != 0
            or (self.active_since is None) != (self.state == "INACTIVE")
            or (
                self.active_since is not None
                and (
                    self.active_since.utcoffset().total_seconds() != 0
                    or self.active_since > self.evaluated_at
                    or (
                        self.state == "FIRING"
                        and (self.evaluated_at - self.active_since).total_seconds() < 120
                    )
                )
            )
        ):
            raise ValueError("Coherent selected original rule required")
        return self


class MonitoringObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    source_kind: Literal["LOCAL_PROMETHEUS_RULE_EVALUATION"] = "LOCAL_PROMETHEUS_RULE_EVALUATION"
    durable: Literal[False] = False
    notification_verified: Literal[False] = False
    independently_attested: Literal[False] = False
    observed_at: AwareDatetime
    state: Literal["DISABLED", "UNAVAILABLE", "AVAILABLE"]
    rules: tuple[MonitoringRule, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def coherent(self):
        if self.observed_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC observation required")
        if self.state == "AVAILABLE":
            if tuple(rule.name for rule in self.rules) != tuple(SPEC) or any(
                not 0 <= (self.observed_at - rule.evaluated_at).total_seconds() <= 45
                for rule in self.rules
            ):
                raise ValueError("Complete fresh selected rules required")
        elif self.rules:
            raise ValueError("Unavailable source cannot infer rule state")
        return self


def _unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Ambiguous observation")
        value[name] = item
    return value


def _constant(value):
    raise ValueError("Finite JSON required")


def _utc(raw):
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Aware source time required")
    return value.astimezone(timezone.utc)


def _select(raw):
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if value["status"] != "success":
        raise ValueError("Successful source required")
    groups = value["data"]["groups"]
    if not isinstance(groups, list) or len(groups) > 16:
        raise ValueError("Bounded source groups required")
    selected = [group for group in groups if group["name"] == "paper-http-monitoring"]
    if len(selected) != 1 or len(selected[0]["rules"]) != 2:
        raise ValueError("Unique selected rule group required")
    rules = {rule["name"]: rule for rule in selected[0]["rules"]}
    if set(rules) != set(SPEC):
        raise ValueError("Complete selected rule names required")
    result = []
    for name, (query, summary, runbook) in SPEC.items():
        rule = rules[name]
        annotations = {"summary": summary, "runbook_path": runbook}
        if (
            rule["query"] != query
            or type(rule["duration"]) not in (int, float)
            or rule["duration"] != 120
            or rule["health"] != "ok"
            or rule.get("lastError", "")
            or rule["labels"] != {"severity": "warning"}
            or rule["annotations"] != annotations
        ):
            raise ValueError("Original healthy rule specification required")
        state, alerts = rule["state"], rule["alerts"]
        if state not in ("inactive", "pending", "firing") or not isinstance(alerts, list):
            raise ValueError("Original rule state required")
        if len(alerts) != (0 if state == "inactive" else 1):
            raise ValueError("Single-source original active observations required")
        active = None
        if alerts:
            alert = alerts[0]
            labels = dict(alert["labels"])
            if name == "PaperHttpScrapeFailed":
                instance = labels.pop("instance")
                host, port = instance.split(":")
                if host != "127.0.0.1" or not port.isdecimal() or not 1 <= int(port) <= 65535:
                    raise ValueError("Original loopback target required")
            if (
                labels != {"alertname": name, "job": "paper_http", "severity": "warning"}
                or alert["state"] != state
                or alert["annotations"] != annotations
            ):
                raise ValueError("Selected original alert labels required")
            active = _utc(alert["activeAt"])
        result.append(
            MonitoringRule(
                name=name,
                state=state.upper(),
                evaluated_at=_utc(rule["lastEvaluation"]),
                active_since=active,
                runbook_path=runbook,
            )
        )
    return tuple(result)


class MonitoringAlertSource:
    def __init__(self, *, enabled=False, port=9090, clock=None, transport=None):
        if type(enabled) is not bool or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Explicit local source configuration required")
        self.enabled = enabled
        self.url = f"http://127.0.0.1:{port}/api/v1/rules?type=alert"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.transport = transport
        self._slots = asyncio.Semaphore(4)

    async def observe(self):
        if not self.enabled:
            return MonitoringObservation(observed_at=self.clock(), state="DISABLED")
        try:
            async with asyncio.timeout(2.5), self._slots:
                async with httpx.AsyncClient(
                    timeout=2,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                    headers={"Accept-Encoding": "identity"},
                ) as client:
                    async with client.stream("GET", self.url) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("Content-Encoding", "identity") != "identity"
                        ):
                            raise ValueError("Uncompressed successful source required")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > 262144:
                                raise ValueError("Observation byte bound exceeded")
                            body.extend(chunk)
            rules = _select(body)
            return MonitoringObservation(observed_at=self.clock(), state="AVAILABLE", rules=rules)
        except Exception:
            return MonitoringObservation(observed_at=self.clock(), state="UNAVAILABLE")


def install_monitoring_alert_routes(
    app: FastAPI, source: MonitoringAlertSource, require_operator: Callable, journal=None
):
    @app.get(
        "/api/v1/observability/alerts",
        response_model=MonitoringObservation,
        dependencies=[Depends(require_operator)],
    )
    async def read(
        response: Response,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        result = await source.observe()
        require_operator(x_operator_token, x_operator_session)
        if journal is not None:
            try:
                journal.record(result)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
                raise HTTPException(503, "Monitoring journal could not be verified") from error
        response.headers["Cache-Control"] = "no-store"
        return result
