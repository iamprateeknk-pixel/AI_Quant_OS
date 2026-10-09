"""Fail-closed routine-call admission; no recovery spending or provider billing claim."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from ai_quant_os.agent_call_policy import AgentCallPolicy, AgentCallRule
from ai_quant_os.agent_call_store import AgentCallStore
from ai_quant_os.agent_registry import ToolInvocation

__all__ = ["AgentCallBudget", "AgentCallPolicy", "AgentCallRule"]


class AgentCallBudget:
    def __init__(
        self, path: Path | None, *, policy: AgentCallPolicy | None, clock: Callable[[], datetime]
    ) -> None:
        self._path = path
        self._clock = clock
        self._store = None
        self._enabled = policy is not None
        if not self._enabled:
            try:
                self._enabled = path is not None and (
                    path.exists() or path.with_suffix(path.suffix + ".pin.json").exists()
                )
            except OSError:
                self._enabled = True
                return
        if self._enabled:
            try:
                if path is None:
                    raise ValueError("Persistent call budget path required")
                self._store = AgentCallStore(path, policy)
            except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
                self._store = None

    def reserve(self, invocation: ToolInvocation) -> None:
        self._restore_if_archive_exists()
        if not self._enabled:
            return
        try:
            if self._store is None:
                raise ValueError("Call budget archive unavailable")
            self._store.reserve(invocation, self._clock())
        except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
            raise PermissionError("Agent call budget unavailable") from None

    def status(self) -> dict[str, object]:
        self._restore_if_archive_exists()
        if not self._enabled:
            return {"state": "DISABLED", "agents": []}
        try:
            if self._store is None:
                raise ValueError("Call budget archive unavailable")
            return self._store.status(self._clock())
        except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
            return {"state": "UNAVAILABLE", "agents": []}

    def _restore_if_archive_exists(self) -> None:
        if self._enabled or self._path is None:
            return
        try:
            present = (
                self._path.exists()
                or self._path.with_suffix(self._path.suffix + ".pin.json").exists()
            )
        except OSError:
            self._enabled = True
            return
        if present:
            self._enabled = True
            try:
                self._store = AgentCallStore(self._path, None)
            except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
                self._store = None
