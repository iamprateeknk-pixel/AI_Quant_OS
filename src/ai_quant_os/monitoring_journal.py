"""Bounded, audit-chained custody for original local monitoring observations."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from uuid import UUID, uuid5, NAMESPACE_URL

from ai_quant_os.audit_chain import AuditChain
from ai_quant_os.monitoring_alerts import MonitoringObservation, SPEC

_JOURNAL_ID = "ai-quant-monitoring-journal-v1"
_MAX_EVENTS = 8192
_SOURCE_UNAVAILABLE = "MonitoringRuleEvaluationUnavailable"
_SOURCE_RUNBOOK = "docs/runbooks/paper-http-monitoring.md#source-unavailable"


class MonitoringJournalConflict(ValueError):
    """The caller's action or journal head no longer matches the stored record."""


@dataclass(frozen=True)
class MonitoringAcknowledgement:
    action_id: UUID
    episode_id: UUID
    actor_session_id: UUID
    acknowledged_at: datetime
    source_head: str
    committed: bool = True
    control_authorized: bool = False


@dataclass(frozen=True)
class MonitoringEpisode:
    episode_id: UUID
    rule_name: str
    first_observed_at: datetime
    active_since: datetime
    state: str
    last_observed_at: datetime
    runbook_path: str
    acknowledgement: MonitoringAcknowledgement | None = None


@dataclass(frozen=True)
class MonitoringJournalHistory:
    episodes: tuple[MonitoringEpisode, ...]
    head_hash: str


class MonitoringJournal:
    """An append-only local projection; acknowledgements never alter paper controls."""

    def __init__(self, path: Path | None = None, *, capacity: int = _MAX_EVENTS):
        if type(capacity) is not int or not 1 <= capacity <= _MAX_EVENTS:
            raise ValueError("Invalid monitoring journal capacity")
        existed = path is not None and path.exists()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity = capacity
        self._lock = Lock()
        self._db = sqlite3.connect(
            str(path) if path else ":memory:",
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._audit = AuditChain(self._db)
        try:
            with self._transaction():
                tables = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {"monitoring_metadata", "audit_events", "audit_chain", "audit_head"}
                if existed and (
                    not required <= tables
                    or self._db.execute("SELECT COUNT(*) FROM audit_head").fetchone()[0] != 1
                ):
                    raise ValueError("Monitoring journal metadata missing")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS monitoring_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), journal_id TEXT NOT NULL, capacity INTEGER NOT NULL)"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (event_key TEXT PRIMARY KEY,event_json TEXT NOT NULL)"
                )
                metadata = self._db.execute(
                    "SELECT journal_id,capacity FROM monitoring_metadata WHERE singleton=1"
                ).fetchone()
                if metadata is None:
                    if existed:
                        raise ValueError("Monitoring journal metadata missing")
                    self._db.execute(
                        "INSERT INTO monitoring_metadata VALUES (1,?,?)", (_JOURNAL_ID, capacity)
                    )
                elif metadata != (_JOURNAL_ID, capacity):
                    raise ValueError("Monitoring journal identity or capacity changed")
                self._audit.initialize()
                self._verified()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _episode_id(rule_name: str, active_since: datetime) -> UUID:
        return uuid5(NAMESPACE_URL, f"ai-quant-monitoring:{rule_name}:{active_since.isoformat()}")

    @staticmethod
    def _utc(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() == timedelta(0)

    def _verified(self) -> tuple[dict[UUID, MonitoringEpisode], str]:
        metadata = self._db.execute(
            "SELECT journal_id,capacity FROM monitoring_metadata WHERE singleton=1"
        ).fetchone()
        if metadata != (_JOURNAL_ID, self._capacity):
            raise ValueError("Monitoring journal metadata changed")
        count, largest = self._db.execute(
            "SELECT COUNT(*),COALESCE(MAX(LENGTH(CAST(event_json AS BLOB))),0) FROM audit_events"
        ).fetchone()
        if count > self._capacity or largest > 4096:
            raise ValueError("Monitoring journal capacity exceeded")
        integrity = self._audit.require_valid()
        if integrity.legacy_event_count:
            raise ValueError("Monitoring journal contains unaudited events")
        episodes: dict[UUID, MonitoringEpisode] = {}
        actions: dict[UUID, MonitoringAcknowledgement] = {}
        for sequence, (key, raw) in enumerate(
            self._db.execute("SELECT event_key,event_json FROM audit_events ORDER BY rowid"),
            start=1,
        ):
            event = json.loads(raw)
            action = event.get("action")
            if action == "OBSERVATION_OPEN" and set(event) == {
                "action",
                "episode_id",
                "rule_name",
                "first_observed_at",
                "active_since",
                "state",
                "last_observed_at",
                "runbook_path",
            }:
                identity = UUID(event["episode_id"])
                name = event["rule_name"]
                first = datetime.fromisoformat(event["first_observed_at"])
                active = datetime.fromisoformat(event["active_since"])
                observed = datetime.fromisoformat(event["last_observed_at"])
                if (
                    key != "episode-open:" + str(identity)
                    or (name not in SPEC and name != _SOURCE_UNAVAILABLE)
                    or identity != self._episode_id(name, active)
                    or event["state"] not in {"PENDING", "FIRING"}
                    or event["runbook_path"] != (SPEC[name][2] if name in SPEC else _SOURCE_RUNBOOK)
                    or not all(self._utc(value) for value in (first, active, observed))
                    or active > first
                    or first > observed
                    or (event["state"] == "FIRING" and (observed - active).total_seconds() < 120)
                    or identity in episodes
                ):
                    raise ValueError("Invalid monitoring episode opening")
                episodes[identity] = MonitoringEpisode(
                    identity, name, first, active, event["state"], observed, event["runbook_path"]
                )
            elif action == "OBSERVATION_STATE" and set(event) == {
                "action",
                "episode_id",
                "state",
                "observed_at",
            }:
                identity = UUID(event["episode_id"])
                observed = datetime.fromisoformat(event["observed_at"])
                prior = episodes.get(identity)
                state = event["state"]
                if (
                    prior is None
                    or key != f"episode-state:{identity}:{sequence}:{state}"
                    or state
                    not in {
                        "PENDING",
                        "FIRING",
                        "NOT_CURRENTLY_OBSERVED",
                        "OBSERVED_INACTIVE",
                        "OBSERVED_REPLACED",
                    }
                    or not self._utc(observed)
                    or state == (prior.state if prior else None)
                    or observed < prior.last_observed_at
                    or (state == "FIRING" and (observed - prior.active_since).total_seconds() < 120)
                ):
                    raise ValueError("Invalid monitoring episode state change")
                episodes[identity] = MonitoringEpisode(
                    prior.episode_id,
                    prior.rule_name,
                    prior.first_observed_at,
                    prior.active_since,
                    state,
                    observed,
                    prior.runbook_path,
                    prior.acknowledgement,
                )
            elif action == "MONITORING_ACKNOWLEDGED" and set(event) == {
                "action",
                "action_id",
                "episode_id",
                "actor_session_id",
                "acknowledged_at",
                "source_head",
                "control_authorized",
            }:
                action_id, identity = UUID(event["action_id"]), UUID(event["episode_id"])
                actor = UUID(event["actor_session_id"])
                at = datetime.fromisoformat(event["acknowledged_at"])
                previous = self._db.execute(
                    "SELECT previous_hash FROM audit_chain WHERE event_key=?", (key,)
                ).fetchone()
                prior = episodes.get(identity)
                receipt = MonitoringAcknowledgement(
                    action_id, identity, actor, at, event["source_head"], True, False
                )
                if (
                    key != "monitoring-ack:" + str(action_id)
                    or prior is None
                    or prior.acknowledgement is not None
                    or action_id.version != 4
                    or actor.version != 4
                    or not self._utc(at)
                    or at < prior.last_observed_at
                    or event["control_authorized"] is not False
                    or not re.fullmatch(r"[0-9a-f]{64}", event["source_head"])
                    or previous != (event["source_head"],)
                    or action_id in actions
                ):
                    raise ValueError("Invalid monitoring acknowledgement")
                actions[action_id] = receipt
                episodes[identity] = MonitoringEpisode(
                    prior.episode_id,
                    prior.rule_name,
                    prior.first_observed_at,
                    prior.active_since,
                    prior.state,
                    prior.last_observed_at,
                    prior.runbook_path,
                    receipt,
                )
            else:
                raise ValueError("Invalid monitoring journal event")
        return episodes, integrity.head_hash

    def history(self) -> MonitoringJournalHistory:
        with self._lock, self._transaction():
            episodes, head = self._verified()
            return self._history(episodes, head)

    @staticmethod
    def _history(episodes, head):
        return MonitoringJournalHistory(
            tuple(
                sorted(
                    episodes.values(),
                    key=lambda item: (item.first_observed_at, str(item.episode_id)),
                )
            ),
            head,
        )

    def record(self, observation: MonitoringObservation) -> MonitoringJournalHistory:
        observation = MonitoringObservation.model_validate(observation)
        with self._lock, self._transaction():
            episodes, head = self._verified()
            latest = max((item.last_observed_at for item in episodes.values()), default=None)
            if latest is not None and observation.observed_at < latest:
                raise ValueError("Monitoring source clock regressed")
            desired = {}
            if observation.state == "AVAILABLE":
                for rule in observation.rules:
                    if rule.state != "INACTIVE":
                        desired[self._episode_id(rule.name, rule.active_since)] = rule
            for identity, episode in tuple(episodes.items()):
                if episode.rule_name == _SOURCE_UNAVAILABLE:
                    continue
                rule = next(
                    (item for item in observation.rules if item.name == episode.rule_name), None
                )
                if observation.state != "AVAILABLE":
                    state = (
                        "NOT_CURRENTLY_OBSERVED"
                        if episode.state in {"PENDING", "FIRING", "NOT_CURRENTLY_OBSERVED"}
                        else episode.state
                    )
                elif rule is None or rule.state == "INACTIVE":
                    state = (
                        "OBSERVED_INACTIVE"
                        if episode.state != "OBSERVED_INACTIVE"
                        else episode.state
                    )
                elif identity in desired:
                    state = rule.state
                else:
                    state = "OBSERVED_REPLACED"
                if state != episode.state:
                    self._append_state(identity, state, observation.observed_at)
            episodes, _ = self._verified()
            source_episodes = sorted(
                (item for item in episodes.values() if item.rule_name == _SOURCE_UNAVAILABLE),
                key=lambda item: (item.first_observed_at, str(item.episode_id)),
            )
            latest_source = source_episodes[-1] if source_episodes else None
            if observation.state == "UNAVAILABLE":
                if latest_source and latest_source.state in {"PENDING", "FIRING"}:
                    state = (
                        "FIRING"
                        if (observation.observed_at - latest_source.active_since).total_seconds()
                        >= 120
                        else "PENDING"
                    )
                    if state != latest_source.state:
                        self._append_state(latest_source.episode_id, state, observation.observed_at)
                else:
                    identity = self._episode_id(_SOURCE_UNAVAILABLE, observation.observed_at)
                    if identity in episodes:
                        raise ValueError("Monitoring source episode timestamp did not advance")
                    if self._audit.require_valid().event_count >= self._capacity:
                        raise ValueError("Monitoring journal capacity exceeded")
                    self._audit.append(
                        "episode-open:" + str(identity),
                        {
                            "action": "OBSERVATION_OPEN",
                            "episode_id": str(identity),
                            "rule_name": _SOURCE_UNAVAILABLE,
                            "first_observed_at": observation.observed_at.isoformat(),
                            "active_since": observation.observed_at.isoformat(),
                            "state": "PENDING",
                            "last_observed_at": observation.observed_at.isoformat(),
                            "runbook_path": _SOURCE_RUNBOOK,
                        },
                    )
            elif latest_source and latest_source.state not in {
                "OBSERVED_INACTIVE",
                "OBSERVED_REPLACED",
            }:
                state = (
                    "OBSERVED_INACTIVE"
                    if observation.state == "AVAILABLE"
                    else "NOT_CURRENTLY_OBSERVED"
                )
                self._append_state(latest_source.episode_id, state, observation.observed_at)
            episodes, _ = self._verified()
            for identity, rule in desired.items():
                if identity not in episodes:
                    if self._audit.require_valid().event_count >= self._capacity:
                        raise ValueError("Monitoring journal capacity exceeded")
                    self._audit.append(
                        "episode-open:" + str(identity),
                        {
                            "action": "OBSERVATION_OPEN",
                            "episode_id": str(identity),
                            "rule_name": rule.name,
                            "first_observed_at": observation.observed_at.isoformat(),
                            "active_since": rule.active_since.isoformat(),
                            "state": rule.state,
                            "last_observed_at": observation.observed_at.isoformat(),
                            "runbook_path": rule.runbook_path,
                        },
                    )
            episodes, head = self._verified()
            return self._history(episodes, head)

    def _append_state(self, identity: UUID, state: str, observed_at: datetime) -> None:
        integrity = self._audit.require_valid()
        if integrity.event_count >= self._capacity:
            raise ValueError("Monitoring journal capacity exceeded")
        ordinal = integrity.event_count + 1
        self._audit.append(
            f"episode-state:{identity}:{ordinal}:{state}",
            {
                "action": "OBSERVATION_STATE",
                "episode_id": str(identity),
                "state": state,
                "observed_at": observed_at.isoformat(),
            },
        )

    def acknowledge(
        self, episode_id, *, action_id, actor_session_id, expected_head, now, authorize
    ):
        identity, action, actor_id = (
            UUID(str(episode_id)),
            UUID(str(action_id)),
            UUID(str(actor_session_id)),
        )
        if action.version != 4 or actor_id.version != 4:
            raise ValueError("Acknowledgement requires original UUIDv4 identities")
        if not self._utc(now) or not re.fullmatch(r"[0-9a-f]{64}", expected_head):
            raise ValueError("UTC acknowledgement and journal head required")
        with self._lock, self._transaction():
            episodes, head = self._verified()
            prior = next(
                (
                    item.acknowledgement
                    for item in episodes.values()
                    if item.acknowledgement and item.acknowledgement.action_id == action
                ),
                None,
            )
            if prior is not None:
                if (prior.episode_id, prior.actor_session_id) != (identity, actor_id):
                    raise MonitoringJournalConflict("Acknowledgement action identity conflict")
                if UUID(str(authorize())) != actor_id:
                    raise PermissionError("Current operator session required")
                return prior
            if UUID(str(authorize())) != actor_id:
                raise PermissionError("Current operator session required")
            if head != expected_head or identity not in episodes:
                raise MonitoringJournalConflict("Monitoring journal head or episode changed")
            if now < episodes[identity].last_observed_at:
                raise MonitoringJournalConflict(
                    "Acknowledgement time precedes the recorded episode"
                )
            if episodes[identity].acknowledgement is not None:
                raise MonitoringJournalConflict("Monitoring episode already has a review receipt")
            if self._audit.require_valid().event_count >= self._capacity:
                raise ValueError("Monitoring journal capacity exceeded")
            if UUID(str(authorize())) != actor_id:
                raise PermissionError("Operator session expired before commit")
            event = {
                "action": "MONITORING_ACKNOWLEDGED",
                "action_id": str(action),
                "episode_id": str(identity),
                "actor_session_id": str(actor_id),
                "acknowledged_at": now.isoformat(),
                "source_head": expected_head,
                "control_authorized": False,
            }
            self._audit.append("monitoring-ack:" + str(action), event)
            return next(
                item for item in self._verified()[0].values() if item.episode_id == identity
            ).acknowledgement

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
