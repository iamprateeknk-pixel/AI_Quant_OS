"""Owned SDK parentage cannot adopt another runtime or ambient application."""

import asyncio

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, use_span

from ai_quant_os.paper_telemetry import PaperTelemetry


@pytest.fixture
def runtime():
    exporter = InMemorySpanExporter()
    telemetry = PaperTelemetry(exporter=exporter)
    try:
        yield telemetry, exporter
    finally:
        telemetry.close()


def test_internal_step_rejects_ambient_foreign_parent(runtime):
    telemetry, exporter = runtime
    foreign = NonRecordingSpan(
        SpanContext(0x11111111111111111111111111111111, 0x2222222222222222, True, TraceFlags(1))
    )
    with use_span(foreign, end_on_exit=False):
        with telemetry.span("paper.risk.evaluate") as step:
            observed = step.context
    assert telemetry.flush()
    actual = exporter.get_finished_spans()[0]
    assert actual.parent is None
    assert actual.context.trace_id != foreign.get_span_context().trace_id
    assert observed["sdk_trace_id"] == format(actual.context.trace_id, "032x")


def test_nested_runtime_uses_its_original_parent_and_restores_it(runtime):
    telemetry, exporter = runtime
    other_exporter = InMemorySpanExporter()
    other = PaperTelemetry(exporter=other_exporter)
    try:
        with telemetry.span("paper.http", root=True) as first:
            first_context = first.context
            with other.span("paper.market.snapshot") as second:
                second_context = second.context
                with telemetry.span("paper.risk.evaluate"):
                    pass
                with other.span("paper.order.submit"):
                    pass
            with telemetry.span("paper.order.submit"):
                pass
        assert telemetry.flush() and other.flush()
        own = exporter.get_finished_spans()
        foreign = other_exporter.get_finished_spans()
        second_root = next(span for span in foreign if span.name == "paper.market.snapshot")
        assert second_root.parent is None
        assert second_context["sdk_trace_id"] != first_context["sdk_trace_id"]
        assert all(
            span.parent.span_id == int(first_context["sdk_span_id"], 16)
            and span.context.trace_id == int(first_context["sdk_trace_id"], 16)
            for span in own
            if span.name != "paper.http"
        )
        child = next(span for span in foreign if span.name == "paper.order.submit")
        assert child.parent.span_id == second_root.context.span_id
        assert child.context.trace_id == second_root.context.trace_id
    finally:
        other.close()


def test_foreign_ambient_span_cannot_replace_owned_parent(runtime):
    telemetry, exporter = runtime
    foreign = NonRecordingSpan(
        SpanContext(0x11111111111111111111111111111111, 0x2222222222222222, False, TraceFlags(1))
    )
    with telemetry.span("paper.http", root=True) as request:
        original = request.context
        with use_span(foreign, end_on_exit=False):
            with telemetry.span("paper.risk.evaluate"):
                pass
    assert telemetry.flush()
    child = next(
        span for span in exporter.get_finished_spans() if span.name == "paper.risk.evaluate"
    )
    assert child.context.trace_id == int(original["sdk_trace_id"], 16)
    assert child.parent.span_id == int(original["sdk_span_id"], 16)


def test_parallel_tasks_and_propagated_thread_keep_request_parentage(runtime):
    telemetry, exporter = runtime

    def thread_step():
        with telemetry.span("paper.market.snapshot"):
            pass

    async def request(ready, proceed):
        with telemetry.span("paper.http", root=True) as root:
            original = root.context
            ready.set()
            await proceed.wait()
            with telemetry.span("paper.agent.review") as review:
                review_context = review.context
                await asyncio.to_thread(thread_step)
                with telemetry.span("paper.provider.review"):
                    await asyncio.sleep(0)
            with telemetry.span("paper.agent.archive"):
                pass
            return original, review_context

    async def run():
        ready_a, ready_b, proceed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with asyncio.TaskGroup() as group:
            a = group.create_task(request(ready_a, proceed))
            b = group.create_task(request(ready_b, proceed))
            await ready_a.wait()
            await ready_b.wait()
            proceed.set()
        return [a.result(), b.result()]

    roots = asyncio.run(run())
    assert roots[0][0]["sdk_trace_id"] != roots[1][0]["sdk_trace_id"]
    assert telemetry.flush()
    spans = exporter.get_finished_spans()
    assert len(spans) == 10
    for original, review in roots:
        request_spans = [
            s for s in spans if s.context.trace_id == int(original["sdk_trace_id"], 16)
        ]
        assert len(request_spans) == 5
        for span in request_spans:
            if span.name == "paper.http":
                assert span.parent is None
            else:
                expected = (
                    review
                    if span.name in {"paper.market.snapshot", "paper.provider.review"}
                    else original
                )
                assert span.parent.span_id == int(expected["sdk_span_id"], 16)


def test_failed_nested_operation_restores_parent_and_later_steps_start_fresh(runtime):
    telemetry, exporter = runtime
    with telemetry.span("paper.http", root=True) as request:
        original = request.context
        with pytest.raises(RuntimeError, match="private-step-failure"):
            with telemetry.span("paper.agent.review"):
                raise RuntimeError("private-step-failure")
        with telemetry.span("paper.agent.archive"):
            pass
    with telemetry.span("paper.market.snapshot"):
        pass
    assert telemetry.flush()
    spans = exporter.get_finished_spans()
    archive = next(s for s in spans if s.name == "paper.agent.archive")
    standalone = next(s for s in spans if s.name == "paper.market.snapshot")
    assert archive.parent.span_id == int(original["sdk_span_id"], 16)
    assert standalone.parent is None
    assert standalone.context.trace_id != int(original["sdk_trace_id"], 16)
    assert "private-step-failure" not in " ".join(s.to_json() for s in spans)
