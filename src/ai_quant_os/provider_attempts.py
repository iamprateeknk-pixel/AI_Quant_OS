"""Process-local request admission, circuit state and reported token accounting."""

from threading import Lock
from time import monotonic
from typing import Callable


class ProviderAttempts:
    def __init__(
        self,
        *,
        max_requests: int,
        failure_threshold: int,
        cooldown_seconds: float,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 1 <= max_requests <= 100000 or not 1 <= failure_threshold <= 100:
            raise ValueError("Invalid provider request or failure limit")
        if not 0 < cooldown_seconds <= 3600:
            raise ValueError("Invalid provider circuit cooldown")
        self._max = max_requests
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = monotonic_clock
        self._lock = Lock()
        self._requests = self._failures = self._consecutive = self._generation = 0
        self._input = self._output = self._reports = 0
        self._open_until = 0.0
        self._probe = False

    def reserve(self) -> int:
        with self._lock:
            if self._requests >= self._max:
                raise ValueError("Provider request budget exhausted")
            if self._clock() < self._open_until or self._probe:
                raise ValueError("Provider circuit unavailable")
            if self._open_until:
                self._probe = True
            self._requests += 1
            return self._generation

    def usage(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self._input += input_tokens
            self._output += output_tokens
            self._reports += 1

    def finish(self, generation: int, *, failed: bool) -> None:
        with self._lock:
            self._failures += int(failed)
            if generation != self._generation:
                return
            self._probe = False
            if failed:
                self._consecutive += 1
                if self._consecutive >= self._threshold:
                    self._open_until = self._clock() + self._cooldown
                    self._generation += 1
            else:
                self._consecutive = 0
                self._open_until = 0.0

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "request_count": self._requests,
                "failure_count": self._failures,
                "unreported_usage_count": self._requests - self._reports,
                "input_tokens": self._input,
                "output_tokens": self._output,
                "budget_exhausted": self._requests >= self._max,
                "circuit_state": "OPEN"
                if self._clock() < self._open_until
                else "HALF_OPEN"
                if self._open_until
                else "CLOSED",
            }
