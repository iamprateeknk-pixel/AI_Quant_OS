"""Owned cumulative native SDK HTTP metrics with bounded Prometheus exposition."""

import math
import re
from threading import RLock

BOUNDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
DURATION = "ai_quant_paper_http_duration_seconds"
FAILURES = "ai_quant_paper_http_failures_total"
MAX_SERIES = 128


class PaperHttpMetrics:
    def __init__(self):
        from opentelemetry.sdk.metrics import (
            AlwaysOffExemplarFilter,
            Counter,
            Histogram,
            MeterProvider,
        )
        from opentelemetry.sdk.metrics.export import AggregationTemporality, InMemoryMetricReader
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
        from opentelemetry.sdk.resources import Resource

        self._lock = RLock()
        self._closed = False
        self._series = set()
        self._reader = InMemoryMetricReader(
            preferred_temporality={
                Counter: AggregationTemporality.CUMULATIVE,
                Histogram: AggregationTemporality.CUMULATIVE,
            }
        )
        self._provider = MeterProvider(
            metric_readers=[self._reader],
            resource=Resource(
                {
                    "service.name": "ai-quant-paper",
                    "service.version": "0.1.0",
                    "ai_quant.mode": "PAPER",
                }
            ),
            exemplar_filter=AlwaysOffExemplarFilter(),
            shutdown_on_exit=False,
            views=[
                View(
                    instrument_name=DURATION,
                    aggregation=ExplicitBucketHistogramAggregation(BOUNDS, record_min_max=False),
                )
            ],
        )
        meter = self._provider.get_meter("ai_quant_os.paper.http", "1.0")
        self._duration = meter.create_histogram(DURATION, unit="s")
        self._failures = meter.create_counter(FAILURES)

    def record(self, route, method, status, elapsed_ns, failed):
        if (
            type(elapsed_ns) is not int
            or not 0 <= elapsed_ns <= 2**63 - 1
            or type(failed) is not bool
        ):
            raise ValueError("Valid native HTTP observation required")
        route = (
            route
            if isinstance(route, str) and re.fullmatch(r"/api/v1/[A-Za-z0-9_/{}/.-]{1,240}", route)
            else "UNMATCHED"
        )
        method = method if method in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "OTHER"
        status_class = (
            f"{status // 100}xx" if type(status) is int and 100 <= status <= 599 else "UNKNOWN"
        )
        with self._lock:
            if self._closed:
                return
            key = (method, route, status_class)
            if key not in self._series:
                if len(self._series) < MAX_SERIES:
                    self._series.add(key)
                else:
                    key = (method, "OTHER_ROUTE", status_class)
            labels = dict(zip(("method", "route", "status_class"), key))
            self._duration.record(elapsed_ns / 1_000_000_000, labels)
            self._failures.add(int(failed), labels)

    def render(self):
        with self._lock:
            if self._closed:
                raise ValueError("Original metrics runtime closed")
            snapshot = self._reader.get_metrics_data()
            observations = {DURATION: {}, FAILURES: {}}
            if snapshot:
                for resource in snapshot.resource_metrics:
                    for scope in resource.scope_metrics:
                        for metric in scope.metrics:
                            if metric.name not in observations:
                                raise ValueError("Unknown original metric")
                            for point in metric.data.data_points:
                                labels = tuple(
                                    point.attributes[k] for k in ("method", "route", "status_class")
                                )
                                if (
                                    set(point.attributes) != {"method", "route", "status_class"}
                                    or labels in observations[metric.name]
                                ):
                                    raise ValueError("Invalid original labels")
                                if labels not in self._series and not (
                                    labels[0] in {"GET", "POST", "PUT", "PATCH", "DELETE", "OTHER"}
                                    and labels[1] == "OTHER_ROUTE"
                                    and labels[2] in {"1xx", "2xx", "3xx", "4xx", "5xx", "UNKNOWN"}
                                ):
                                    raise ValueError("Unowned original series")
                                if point.exemplars:
                                    raise ValueError("Metric exemplars not admitted")
                                observations[metric.name][labels] = point
            if (
                set(observations[DURATION]) != set(observations[FAILURES])
                or len(observations[DURATION]) > MAX_SERIES + 36
            ):
                raise ValueError("Original metric inventory differs")
            return _render(observations)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._provider.shutdown()


def _count(value):
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise ValueError("Exact original count required")
    return value


def _render(observations):
    lines = [
        f"# HELP {DURATION} Volatile completed HTTP scope duration; not trading or delivery acceptance.",
        f"# TYPE {DURATION} histogram",
    ]
    counters = []
    for labels, point in sorted(observations[DURATION].items()):
        count = _count(point.count)
        failure_count = _count(observations[FAILURES][labels].value)
        if (
            tuple(point.explicit_bounds) != BOUNDS
            or len(point.bucket_counts) != len(BOUNDS) + 1
            or sum(map(_count, point.bucket_counts)) != count
            or failure_count > count
            or not math.isfinite(point.sum)
            or point.sum < 0
        ):
            raise ValueError("Invalid original SDK histogram")
        # All labels originate from fixed enums or restricted registered route templates.
        attributes = ",".join(
            f'{key}="{value}"' for key, value in zip(("method", "route", "status_class"), labels)
        )
        cumulative = 0
        for bound, bucket in zip((*BOUNDS, "+Inf"), point.bucket_counts):
            cumulative += bucket
            lines.append(f'{DURATION}_bucket{{{attributes},le="{bound}"}} {cumulative}')
        lines.extend(
            [
                f"{DURATION}_sum{{{attributes}}} {point.sum}",
                f"{DURATION}_count{{{attributes}}} {count}",
            ]
        )
        counters.append(f"{FAILURES}{{{attributes}}} {failure_count}")
    lines.extend(
        [
            f"# HELP {FAILURES} Volatile ASGI exception or missing terminal body count; HTTP errors also use status_class.",
            f"# TYPE {FAILURES} counter",
            *counters,
        ]
    )
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > 1_048_576:
        raise ValueError("Bounded original metric response required")
    return text
