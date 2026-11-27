"""The promises of the trace sink port, checked on every sink."""

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from lamplighter.adapters.in_memory import InMemoryTraceSink
from lamplighter.ports import Span, SpanKind, TraceSink

type Held = Callable[[], Coroutine[Any, Any, dict[str, Span]]]


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def in_memory() -> tuple[TraceSink, Held]:
    sink = InMemoryTraceSink()

    async def held() -> dict[str, Span]:
        return {span.span_id: span for span in sink.held}

    return sink, held


SINKS: dict[str, Callable[[], tuple[TraceSink, Held]]] = {"in_memory": in_memory}


@pytest.fixture(params=list(SINKS.values()), ids=list(SINKS))
def made(request: pytest.FixtureRequest) -> tuple[TraceSink, Held]:
    return request.param()


TRACE = "0e9989490d36bd20896dd8df2ee81290"


def span(
    span_id: str,
    parent: str | None = None,
    kind: SpanKind = "step",
    name: str = "step 1",
) -> Span:
    return Span(
        trace_id=TRACE,
        span_id=span_id,
        parent_id=parent,
        name=name,
        kind=kind,
        start_ms=1_795_678_512_100,
        duration_ms=250,
        error=None,
        attributes={"lamp.run_id": "run-1", "lamp.step": 1, "lamp.outside_text": True},
    )


ROOT = span("a" * 16, kind="run", name="invoke_agent lamplighter")
STEP = span("b" * 16, parent=ROOT.span_id)
MODEL = span("c" * 16, parent=STEP.span_id, kind="model", name="chat made-up-1")


def test_an_empty_batch_returns_none_and_reaches_nobody(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made

    assert run(sink.export([])) is None
    assert run(held()) == {}


def test_after_none_the_store_holds_every_span_of_the_batch(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made

    assert run(sink.export([ROOT, STEP, MODEL])) is None
    assert run(held()) == {s.span_id: s for s in (ROOT, STEP, MODEL)}


def test_the_same_batch_again_changes_nothing(made: tuple[TraceSink, Held]) -> None:
    sink, held = made
    run(sink.export([ROOT, STEP]))

    assert run(sink.export([ROOT, STEP])) is None
    assert run(sink.export([STEP])) is None
    assert run(held()) == {s.span_id: s for s in (ROOT, STEP)}


def test_a_known_id_with_other_content_keeps_the_first_copy(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made
    run(sink.export([STEP]))
    other = dataclasses.replace(STEP, name="step 2", duration_ms=999)

    assert run(sink.export([other])) is None
    assert run(held()) == {STEP.span_id: STEP}


def test_a_child_may_come_first_and_a_parent_may_never_come(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made

    assert run(sink.export([MODEL])) is None
    assert run(sink.export([STEP])) is None
    assert run(held()) == {s.span_id: s for s in (MODEL, STEP)}


def test_whether_it_leaves_this_mac_never_changes(made: tuple[TraceSink, Held]) -> None:
    sink, _ = made
    first = sink.leaves_this_mac
    run(sink.export([ROOT]))

    assert sink.leaves_this_mac == first
    assert not first
