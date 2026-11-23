"""Owned native SDK HTTP/agent observations with fixed cardinality limits."""

import math
import re
from threading import RLock

BOUNDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
DURATION = "ai_quant_paper_http_duration_seconds"
FAILURES = "ai_quant_paper_http_failures_total"
MAX_SERIES = 128
AGENT_DURATION = "ai_quant_paper_agent_execution_duration_seconds"
AGENT_EXECUTIONS = "ai_quant_paper_agent_executions_total"
AGENT_IDS = frozenset(f"A{index:02}" for index in range(1, 19))
AGENT_STATUSES = frozenset(
    {
        "COMPLETED",
        "UNAVAILABLE",
        "EXPIRED",
        "DENIED",
        "WAIT_FOR_DATA",
        "INVALID",
        "FAILED",
        "TIMEOUT",
        "UNCERTAIN",
    }
)
MAX_AGENT_SERIES = len(AGENT_IDS) * len(AGENT_STATUSES)


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
                ),
                View(
                    instrument_name=AGENT_DURATION,
                    aggregation=ExplicitBucketHistogramAggregation(BOUNDS, record_min_max=False),
                ),
            ],
        )
        meter = self._provider.get_meter("ai_quant_os.paper.http", "1.0")
        self._duration = meter.create_histogram(DURATION, unit="s")
        self._failures = meter.create_counter(FAILURES)
        self._agent_duration = meter.create_histogram(AGENT_DURATION, unit="s")
        self._agent_executions = meter.create_counter(AGENT_EXECUTIONS)

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

    def record_agent(self, agent_id, status, elapsed_ns):
        if (
            not isinstance(agent_id, str)
            or agent_id not in AGENT_IDS
            or not isinstance(status, str)
            or status not in AGENT_STATUSES
            or (
                status == "UNCERTAIN"
                and elapsed_ns is not None
                or status != "UNCERTAIN"
                and (type(elapsed_ns) is not int or not 0 <= elapsed_ns <= 2**63 - 1)
            )
        ):
            raise ValueError("Valid bounded agent execution observation required")
        with self._lock:
            if self._closed:
                return
            labels = {"agent_id": agent_id, "status": status}
            if elapsed_ns is not None:
                self._agent_duration.record(elapsed_ns / 1_000_000_000, labels)
            self._agent_executions.add(1, labels)

    def render(self):
        with self._lock:
            if self._closed:
                raise ValueError("Original metrics runtime closed")
            snapshot = self._reader.get_metrics_data()
            observations = {
                DURATION: {},
                FAILURES: {},
                AGENT_DURATION: {},
                AGENT_EXECUTIONS: {},
            }
            label_keys = {
                DURATION: ("method", "route", "status_class"),
                FAILURES: ("method", "route", "status_class"),
                AGENT_DURATION: ("agent_id", "status"),
                AGENT_EXECUTIONS: ("agent_id", "status"),
            }
            if snapshot:
                for resource in snapshot.resource_metrics:
                    for scope in resource.scope_metrics:
                        for metric in scope.metrics:
                            if metric.name not in observations:
                                raise ValueError("Unknown original metric")
                            for point in metric.data.data_points:
                                keys = label_keys[metric.name]
                                labels = tuple(point.attributes[key] for key in keys)
                                if (
                                    set(point.attributes) != set(keys)
                                    or labels in observations[metric.name]
                                ):
                                    raise ValueError("Invalid original labels")
                                if metric.name in {DURATION, FAILURES}:
                                    if labels not in self._series and not (
                                        labels[0]
                                        in {"GET", "POST", "PUT", "PATCH", "DELETE", "OTHER"}
                                        and labels[1] == "OTHER_ROUTE"
                                        and labels[2]
                                        in {"1xx", "2xx", "3xx", "4xx", "5xx", "UNKNOWN"}
                                    ):
                                        raise ValueError("Unowned original series")
                                elif labels[0] not in AGENT_IDS or labels[1] not in AGENT_STATUSES:
                                    raise ValueError("Unowned original agent series")
                                if point.exemplars:
                                    raise ValueError("Metric exemplars not admitted")
                                observations[metric.name][labels] = point
            if (
                set(observations[DURATION]) != set(observations[FAILURES])
                or len(observations[DURATION]) > MAX_SERIES + 36
                or not set(observations[AGENT_DURATION]).issubset(
                    set(observations[AGENT_EXECUTIONS])
                )
                or any(
                    (labels[1] == "UNCERTAIN") == (labels in observations[AGENT_DURATION])
                    for labels in observations[AGENT_EXECUTIONS]
                )
                or len(observations[AGENT_DURATION]) > MAX_AGENT_SERIES
                or len(observations[AGENT_EXECUTIONS]) > MAX_AGENT_SERIES
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
        f"# HELP {AGENT_DURATION} Volatile terminal agent graph attempt duration; not model quality or authority.",
        f"# TYPE {AGENT_DURATION} histogram",
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
    agent_counters = []
    for labels, point in sorted(observations[AGENT_DURATION].items()):
        count = _count(point.count)
        recorded = _count(observations[AGENT_EXECUTIONS][labels].value)
        if (
            tuple(point.explicit_bounds) != BOUNDS
            or len(point.bucket_counts) != len(BOUNDS) + 1
            or sum(map(_count, point.bucket_counts)) != count
            or recorded != count
            or not math.isfinite(point.sum)
            or point.sum < 0
        ):
            raise ValueError("Invalid original agent histogram")
        attributes = f'agent_id="{labels[0]}",status="{labels[1]}"'
        cumulative = 0
        for bound, bucket in zip((*BOUNDS, "+Inf"), point.bucket_counts):
            cumulative += bucket
            lines.append(f'{AGENT_DURATION}_bucket{{{attributes},le="{bound}"}} {cumulative}')
        lines.extend(
            [
                f"{AGENT_DURATION}_sum{{{attributes}}} {point.sum}",
                f"{AGENT_DURATION}_count{{{attributes}}} {count}",
            ]
        )
    for labels, point in sorted(observations[AGENT_EXECUTIONS].items()):
        recorded = _count(point.value)
        if labels[1] != "UNCERTAIN":
            duration = observations[AGENT_DURATION].get(labels)
            if duration is None or _count(duration.count) != recorded:
                raise ValueError("Agent execution count/duration mismatch")
        elif labels in observations[AGENT_DURATION]:
            raise ValueError("Uncertain agent status cannot claim execution latency")
        attributes = f'agent_id="{labels[0]}",status="{labels[1]}"'
        agent_counters.append(f"{AGENT_EXECUTIONS}{{{attributes}}} {recorded}")
    lines.extend(
        [
            f"# HELP {AGENT_EXECUTIONS} Volatile terminal agent graph attempt count by fixed agent and status.",
            f"# TYPE {AGENT_EXECUTIONS} counter",
            *agent_counters,
        ]
    )
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > 1_048_576:
        raise ValueError("Bounded original metric response required")
    return text
