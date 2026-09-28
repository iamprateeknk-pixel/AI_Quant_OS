"""Disposable Valkey cache for latest ticks from verified archives."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from redis.exceptions import WatchError

from ai_quant_os.contracts import _require_aware
from ai_quant_os.tick_archive import TickArchive
from ai_quant_os.tick_data import Tick


@dataclass(frozen=True)
class CachedTick:
    tick: Tick
    archive_id: str
    dataset_fingerprint: str


class LatestTickCache:
    def __init__(self, client) -> None:
        self._client = client

    @staticmethod
    def _key(instrument_id: str, source_id: str) -> str:
        identity = json.dumps([instrument_id, source_id], separators=(",", ":"))
        return f"ai_quant_os:latest_tick:v1:{hashlib.sha256(identity.encode()).hexdigest()}"

    @staticmethod
    def _encode(value: CachedTick) -> str:
        tick = value.tick
        return json.dumps(
            {
                "schema_version": 1,
                "archive_id": value.archive_id,
                "dataset_fingerprint": value.dataset_fingerprint,
                "instrument_id": tick.instrument_id,
                "source_id": tick.source_id,
                "sequence": tick.sequence,
                "observed_at": tick.observed_at.isoformat(),
                "bid": str(tick.bid),
                "ask": str(tick.ask),
                "bid_size": str(tick.bid_size),
                "ask_size": str(tick.ask_size),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode(raw: bytes) -> CachedTick:
        data = json.loads(raw)
        if data["schema_version"] != 1:
            raise ValueError("Unsupported cached tick schema")
        tick = Tick(
            instrument_id=data["instrument_id"],
            source_id=data["source_id"],
            sequence=data["sequence"],
            observed_at=datetime.fromisoformat(data["observed_at"]),
            bid=Decimal(data["bid"]),
            ask=Decimal(data["ask"]),
            bid_size=Decimal(data["bid_size"]),
            ask_size=Decimal(data["ask_size"]),
        )
        if any(
            len(data[field]) != 64 or any(c not in "0123456789abcdef" for c in data[field])
            for field in ("archive_id", "dataset_fingerprint")
        ):
            raise ValueError("Invalid cached tick lineage")
        return CachedTick(
            tick=tick,
            archive_id=data["archive_id"],
            dataset_fingerprint=data["dataset_fingerprint"],
        )

    def publish(self, archive: TickArchive, archive_id: str, *, ttl_seconds: int) -> int:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("Cache TTL must be a positive integer")
        dataset = archive.replay(archive_id)
        manifest = archive.manifest(archive_id)
        latest: dict[tuple[str, str], Tick] = {}
        for tick in dataset.ticks:
            latest[(tick.instrument_id, tick.source_id)] = tick
        updated = 0
        for tick in latest.values():
            value = CachedTick(tick, archive_id, manifest.dataset_fingerprint)
            updated += self._put(value, ttl_seconds)
        return updated

    def _put(self, value: CachedTick, ttl_seconds: int) -> int:
        key = self._key(value.tick.instrument_id, value.tick.source_id)
        for _ in range(4):
            try:
                with self._client.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is not None:
                        previous = self._decode(raw)
                        if value.tick.sequence < previous.tick.sequence:
                            return 0
                        if value.tick.sequence == previous.tick.sequence:
                            if value.tick != previous.tick:
                                raise ValueError("Conflicting cached tick sequence")
                            return 0
                        if value.tick.observed_at < previous.tick.observed_at:
                            raise ValueError("Newer tick sequence has an older timestamp")
                    pipe.multi()
                    pipe.set(key, self._encode(value), ex=ttl_seconds)
                    pipe.execute()
                    return 1
            except WatchError:
                continue
        raise RuntimeError("Concurrent tick cache update did not settle")

    def get(
        self,
        instrument_id: str,
        source_id: str,
        *,
        now: datetime,
        max_age: timedelta,
    ) -> CachedTick | None:
        _require_aware(now)
        if max_age.total_seconds() < 0:
            raise ValueError("Maximum age cannot be negative")
        raw = self._client.get(self._key(instrument_id, source_id))
        if raw is None:
            return None
        try:
            value = self._decode(raw)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return None
        if value.tick.instrument_id != instrument_id or value.tick.source_id != source_id:
            return None
        age = now - value.tick.observed_at
        if not timedelta(0) <= age <= max_age:
            return None
        return value
