"""Public SDK span processor with bounded ownership and exact volatile accounting."""

from collections import deque
from threading import Condition, Thread
from time import monotonic
from opentelemetry.sdk.trace import SpanProcessor


class TraceExportQueue(SpanProcessor):
    capacity = 512
    batch_size = 32

    def __init__(self, exporter, *, lock):
        self._exporter = exporter
        self._condition = Condition(lock)
        self._queue = deque()
        self._stopping = False
        self._failed = False
        self._urgent = False
        self._next_export = None
        self._counts = dict.fromkeys(
            (
                "observed_spans",
                "acknowledged_spans",
                "unconfirmed_spans",
                "dropped_spans",
                "pending_spans",
                "inflight_spans",
                "acknowledged_batches",
                "failed_batches",
            ),
            0,
        )
        self._worker = Thread(target=self._run, name="ai-quant-trace-export", daemon=True)
        self._worker.start()

    def on_start(self, span, parent_context=None):
        """SDK starts are not finished/exportable observations."""

    def on_end(self, span):
        with self._condition:
            self._counts["observed_spans"] += 1
            if self._stopping or self._failed or len(self._queue) == self.capacity:
                self._counts["dropped_spans"] += 1
                return
            if not self._queue:
                self._next_export = monotonic() + 1
            self._queue.append(span)
            self._counts["pending_spans"] = len(self._queue)
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            alive = self._worker.is_alive()
            state = (
                ("STOPPING" if alive else "STOPPED")
                if self._stopping
                else ("FAILED" if self._failed or not alive else "RUNNING")
            )
            return {"runtime_state": state, "worker_alive": alive, **self._counts}

    def force_flush(self, timeout_millis=3000):
        if type(timeout_millis) not in (int, float) or not 0 <= timeout_millis <= 60000:
            return False
        deadline = monotonic() + timeout_millis / 1000
        with self._condition:
            if self._stopping or self._failed:
                return False
            self._urgent = True
            self._condition.notify_all()
            while self._queue or self._counts["inflight_spans"]:
                remaining = deadline - monotonic()
                if remaining <= 0 or self._stopping or self._failed:
                    return False
                self._condition.wait(remaining)
            return True

    def shutdown(self):
        with self._condition:
            self._stopping = True
            self._counts["dropped_spans"] += len(self._queue)
            self._queue.clear()
            self._counts["pending_spans"] = 0
            self._condition.notify_all()
        # Keep the original worker handle if an injected exporter exceeds its contract.
        self._worker.join(timeout=3)

    def _run(self):
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            while True:
                with self._condition:
                    if self._stopping:
                        break
                    if not self._queue:
                        self._urgent = False
                        self._condition.wait()
                        continue
                    remaining = self._next_export - monotonic()
                    if not self._urgent and len(self._queue) < self.batch_size and remaining > 0:
                        self._condition.wait(remaining)
                        continue
                    batch = [
                        self._queue.popleft() for _ in range(min(self.batch_size, len(self._queue)))
                    ]
                    self._counts["pending_spans"] = len(self._queue)
                    self._counts["inflight_spans"] = len(batch)
                    self._next_export = monotonic() + 1 if self._queue else None
                try:
                    accepted = self._exporter.export(batch) == SpanExportResult.SUCCESS
                except Exception:
                    accepted = False
                with self._condition:
                    self._counts["inflight_spans"] = 0
                    self._counts["acknowledged_spans" if accepted else "unconfirmed_spans"] += len(
                        batch
                    )
                    self._counts["acknowledged_batches" if accepted else "failed_batches"] += 1
                    self._condition.notify_all()
        except BaseException:
            with self._condition:
                self._failed = True
                if self._counts["inflight_spans"]:
                    self._counts["unconfirmed_spans"] += self._counts["inflight_spans"]
                    self._counts["failed_batches"] += 1
                    self._counts["inflight_spans"] = 0
                self._counts["dropped_spans"] += len(self._queue)
                self._queue.clear()
                self._counts["pending_spans"] = 0
                self._condition.notify_all()
        finally:
            try:
                self._exporter.shutdown()
            except BaseException:
                pass
