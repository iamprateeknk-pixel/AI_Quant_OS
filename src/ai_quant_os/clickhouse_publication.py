"""Publish verified immutable archive snapshots into ClickHouse."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from ai_quant_os.data_archive import BarArchive
from ai_quant_os.tick_archive import TickArchive


@dataclass(frozen=True)
class PublicationResult:
    archive_id: str
    kind: str
    row_count: int
    already_published: bool


class ClickHousePublicationError(RuntimeError):
    """A verified archive could not be safely published."""


class ClickHouseArchivePublisher:
    def __init__(
        self,
        endpoint: str,
        *,
        user: str,
        password: str,
        timeout_seconds: float = 10,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
        ):
            raise ValueError("ClickHouse endpoint must use HTTPS or a loopback HTTP address")
        if not user or not password or timeout_seconds <= 0:
            raise ValueError("ClickHouse credentials and a positive timeout are required")
        self.endpoint = endpoint.rstrip("/")
        self.user = user
        self.password = password
        self.timeout_seconds = timeout_seconds

    def _request(self, query: str, body: bytes | None = None) -> bytes:
        request = Request(
            f"{self.endpoint}/?query={quote(query, safe='')}",
            data=body,
            headers={
                "X-ClickHouse-User": self.user,
                "X-ClickHouse-Key": self.password,
                "Content-Type": "application/jsonEachRow" if body is not None else "text/plain",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except HTTPError as error:
            raise ClickHousePublicationError(
                f"ClickHouse rejected publication request (HTTP {error.code})"
            ) from None
        except URLError:
            raise ClickHousePublicationError("ClickHouse publication request failed") from None

    def _count(self, table: str, archive_id: str) -> int:
        result = self._request(
            f"SELECT count() FROM quant_market.{table} WHERE source_archive_id = '{archive_id}'"
        )
        try:
            count = int(result.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError):
            raise ClickHousePublicationError(
                "ClickHouse returned an invalid publication count"
            ) from None
        if count < 0:
            raise ClickHousePublicationError("ClickHouse returned an invalid publication count")
        return count

    def _publish(self, *, table: str, archive_id: str, rows: list[dict]) -> PublicationResult:
        expected_count = len(rows)
        if not expected_count:
            raise ValueError("Cannot publish an empty archive")
        existing_count = self._count(table, archive_id)
        if existing_count == expected_count:
            return PublicationResult(archive_id, table, expected_count, True)
        if existing_count:
            raise ClickHousePublicationError(
                "ClickHouse contains a partial or duplicate archive; refusing to append rows"
            )
        body = "".join(json.dumps(row, separators=(",", ":"), default=str) + "\n" for row in rows)
        self._request(f"INSERT INTO quant_market.{table} FORMAT JSONEachRow", body.encode())
        committed_count = self._count(table, archive_id)
        if committed_count != expected_count:
            raise ClickHousePublicationError(
                "ClickHouse publication count did not match the verified archive"
            )
        return PublicationResult(archive_id, table, expected_count, False)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    def publish_bars(self, archive: BarArchive, archive_id: str) -> PublicationResult:
        dataset = archive.replay(archive_id)
        manifest = archive.manifest(archive_id)
        rows = [
            {
                "instrument_id": bar.instrument_id,
                "observed_at": self._timestamp(bar.observed_at),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "source_archive_id": archive_id,
                "ingested_at": self._timestamp(manifest.ingested_at),
            }
            for bar in dataset.bars
        ]
        return self._publish(table="bars_v1", archive_id=archive_id, rows=rows)

    def publish_ticks(self, archive: TickArchive, archive_id: str) -> PublicationResult:
        dataset = archive.replay(archive_id)
        manifest = archive.manifest(archive_id)
        rows = [
            {
                "instrument_id": tick.instrument_id,
                "source_id": tick.source_id,
                "sequence": tick.sequence,
                "observed_at": self._timestamp(tick.observed_at),
                "bid": str(tick.bid),
                "ask": str(tick.ask),
                "bid_size": str(tick.bid_size),
                "ask_size": str(tick.ask_size),
                "source_archive_id": archive_id,
                "ingested_at": self._timestamp(manifest.ingested_at),
            }
            for tick in dataset.ticks
        ]
        return self._publish(table="ticks_v1", archive_id=archive_id, rows=rows)
