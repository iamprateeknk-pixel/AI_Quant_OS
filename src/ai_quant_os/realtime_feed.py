"""Bounded full-state paper feed with per-frame operator admission."""

import asyncio
import sqlite3
from datetime import timedelta, timezone
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, UUID4, model_validator

from ai_quant_os.contracts import _require_aware
from ai_quant_os.world_state import WorldState


class RealtimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["PAPER"] = "PAPER"
    epoch: UUID4
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    safety_state: Literal[
        "NORMAL", "CAUTION", "REDUCED_RISK", "NO_NEW_RISK", "FLATTEN_ONLY", "HALT"
    ]
    order_count: int = Field(strict=True, ge=0, le=100000)
    world: WorldState

    @model_validator(mode="after")
    def original_lease(self):
        if not (
            self.captured_at == self.world.as_of
            and self.captured_at <= self.valid_until <= self.captured_at + timedelta(seconds=3)
            and self.valid_until <= self.world.valid_until
            and self.world.source == "PAPER_SIMULATED"
        ):
            raise ValueError("Realtime snapshot source/lease mismatch")
        return self


class RealtimeFeed:
    def __init__(self, capture, *, clock):
        self._capture, self._clock = capture, clock
        self._epoch = uuid4()
        self._sequence = 0
        self._latest_time = None
        self._lock = Lock()
        self._active = 0

    @property
    def active_streams(self):
        with self._lock:
            return self._active

    def snapshot(self):
        with self._lock:
            now = self._clock()
            _require_aware(now)
            now = now.astimezone(timezone.utc)
            if self._latest_time is not None and now < self._latest_time:
                raise ValueError("Realtime source clock regressed")
            safety, count, world = self._capture(now)
            frame = RealtimeSnapshot(
                epoch=self._epoch,
                sequence=self._sequence + 1,
                captured_at=now,
                valid_until=min(now + timedelta(seconds=3), world.valid_until),
                safety_state=safety,
                order_count=count,
                world=world,
            )
            raw = frame.model_dump_json().encode("utf-8")
            if len(raw) > 32768:
                raise ValueError("Realtime snapshot capacity exceeded")
            self._latest_time, self._sequence = now, frame.sequence
            return frame

    async def events(self, authorize, *, frames=20, interval=1):
        if type(frames) is not int or not 1 <= frames <= 20 or not 0 <= interval <= 1:
            raise ValueError("Invalid stream limits")
        with self._lock:
            if self._active >= 8:
                return
            self._active += 1
        try:
            for index in range(frames):

                def capture_authorized():
                    authorize()
                    frame = self.snapshot()
                    authorize()
                    return frame

                try:
                    frame = await asyncio.to_thread(capture_authorized)
                except (
                    HTTPException,
                    ValueError,
                    OSError,
                    TypeError,
                    KeyError,
                    ArithmeticError,
                    sqlite3.Error,
                ):
                    return
                event = "reset" if index == 0 else "snapshot"
                yield (
                    f"retry: 2000\nid: {frame.epoch}:{frame.sequence}\nevent: {event}\ndata: "
                    + frame.model_dump_json()
                    + "\n\n"
                ).encode("utf-8")
                if index + 1 < frames:
                    await asyncio.sleep(interval)
        finally:
            with self._lock:
                self._active -= 1


def install_realtime_routes(app, authorization, feed):
    @app.get(
        "/api/v1/realtime/snapshot",
        response_model=RealtimeSnapshot,
        dependencies=[Depends(authorization.require_operator)],
    )
    def snapshot(response: Response):
        try:
            frame = feed.snapshot()
        except (ValueError, OSError, TypeError, KeyError, ArithmeticError, sqlite3.Error) as error:
            raise HTTPException(503, "Realtime state could not be verified") from error
        response.headers["Cache-Control"] = "no-store"
        return frame

    @app.get("/api/v1/realtime/events", dependencies=[Depends(authorization.require_operator)])
    def events(
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        def authorize():
            authorization.require_operator(x_operator_token, x_operator_session)

        return StreamingResponse(
            feed.events(authorize),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
