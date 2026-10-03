"""Operator publication and verified archival reads for paper event feeds."""

from datetime import datetime
from typing import Callable
import sqlite3

from fastapi import Depends, FastAPI, HTTPException

from ai_quant_os.event_feed import EventFeed, feed_status
from ai_quant_os.event_feed_store import EventFeedStore


def install_event_feed_routes(
    app: FastAPI, store: EventFeedStore, require_operator: Callable, clock: Callable[[], datetime]
) -> None:
    @app.post("/api/v1/paper/events/feed", dependencies=[Depends(require_operator)])
    def publish(feed: EventFeed) -> dict:
        try:
            created = store.put(feed, now=clock())
        except ValueError as error:
            raise HTTPException(status_code=409, detail="Event feed publication refused") from error
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="Event archive unavailable") from error
        return {"mode": "PAPER", "feed_ref": feed.content_ref, "created": created}

    @app.get("/api/v1/events/feed")
    def current() -> dict:
        try:
            feed = store.latest()
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Event archive integrity unavailable"
            ) from error
        return {
            "mode": "PAPER",
            "status": feed_status(feed, clock()),
            "feed_ref": feed.content_ref if feed else None,
            "feed": feed.model_dump(mode="json") if feed else None,
        }

    @app.get("/api/v1/events/feeds/{feed_ref}", dependencies=[Depends(require_operator)])
    def archived(feed_ref: str) -> dict:
        try:
            feed = store.get(feed_ref)
        except (ValueError, sqlite3.Error) as error:
            raise HTTPException(
                status_code=503, detail="Event archive integrity unavailable"
            ) from error
        if feed is None:
            raise HTTPException(status_code=404, detail="Event feed not found")
        return {
            "mode": "PAPER",
            "archival_only": True,
            "feed_ref": feed.content_ref,
            "feed": feed.model_dump(mode="json"),
        }
