"""Bounded process-local counters for canonical ingestion outcomes; no raw prices."""

from threading import Lock

CODES = frozenset(
    {"INVALID_QUOTE", "INVALID_BAR", "CONFLICTING_BAR", "FUTURE_BAR", "DUPLICATE_BAR"}
)


class IngressHealth:
    def __init__(self) -> None:
        self._counts = {code: 0 for code in sorted(CODES)}
        self._lock = Lock()

    def note(self, code: str, count: int = 1) -> None:
        if code not in CODES or not isinstance(count, int) or not 0 <= count <= 1000:
            raise ValueError("Invalid ingress health observation")
        with self._lock:
            self._counts[code] = min(2**31 - 1, self._counts[code] + count)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return self._counts.copy()
