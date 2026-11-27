"""The promises of the trace sink port, checked on every sink."""

import asyncio
import dataclasses
import tempfile
from collections.abc import Callable, Coroutine, Generator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from lamplighter.adapters.in_memory import InMemoryTraceSink
from lamplighter.adapters.phoenix import PhoenixSink
from lamplighter.adapters.system_clock import SystemClock
from lamplighter.ports import Failure, Span, SpanKind, TraceSink
from tests.phoenix_process import running_phoenix
from tests.phoenix_stand_in import stand_in

# What the store holds: the name of each span, by its id.
type Held = Callable[[], dict[str, str]]


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def in_memory() -> Generator[tuple[TraceSink, Held]]:
    sink = InMemoryTraceSink()
    yield sink, lambda: {span.span_id: span.name for span in sink.held}


class _OnePhoenixClient:
    """A Phoenix sink with a new HTTP client for each export.

    Each test step runs in an event loop of its own, and a client belongs
    to the loop it was made in.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.leaves_this_mac = PhoenixSink(
            url, "lamplighter", httpx.AsyncClient(), SystemClock()
        ).leaves_this_mac

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        async with httpx.AsyncClient(trust_env=False) as http:
            # A real Phoenix stores in real time, so the confirm window must be
            # real seconds too.
            sink = PhoenixSink(self.url, "lamplighter", http, SystemClock())
            return await sink.export(spans)


def phoenix() -> Generator[tuple[TraceSink, Held]]:
    with stand_in() as server:
        yield _OnePhoenixClient(server.url), server.names


def real_phoenix() -> Generator[tuple[TraceSink, Held]]:
    with (
        tempfile.TemporaryDirectory() as folder,
        running_phoenix(Path(folder) / "phoenix") as made,
    ):
        yield _OnePhoenixClient(made.url), made.names


SINKS: list[Any] = [
    pytest.param(in_memory, id="in_memory"),
    pytest.param(phoenix, id="phoenix_stand_in"),
    # A real Phoenix is started for each test, so the default run leaves it out.
    pytest.param(real_phoenix, id="real_phoenix", marks=pytest.mark.phoenix),
]


@pytest.fixture(params=SINKS)
def made(request: pytest.FixtureRequest) -> Generator[tuple[TraceSink, Held]]:
    yield from request.param()


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
    assert held() == {}


def test_after_none_the_store_holds_every_span_of_the_batch(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made

    assert run(sink.export([ROOT, STEP, MODEL])) is None
    assert held() == {s.span_id: s.name for s in (ROOT, STEP, MODEL)}


def test_the_same_batch_again_changes_nothing(made: tuple[TraceSink, Held]) -> None:
    sink, held = made
    assert run(sink.export([ROOT, STEP])) is None

    assert run(sink.export([ROOT, STEP])) is None
    assert run(sink.export([STEP])) is None
    assert held() == {s.span_id: s.name for s in (ROOT, STEP)}


def test_a_known_id_with_other_content_keeps_the_first_copy(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made
    assert run(sink.export([STEP])) is None
    other = dataclasses.replace(STEP, name="step 2", duration_ms=999)

    assert run(sink.export([other])) is None
    assert held() == {STEP.span_id: STEP.name}


def test_a_child_may_come_first_and_a_parent_may_never_come(
    made: tuple[TraceSink, Held],
) -> None:
    sink, held = made

    assert run(sink.export([MODEL])) is None
    assert run(sink.export([STEP])) is None
    assert held() == {s.span_id: s.name for s in (MODEL, STEP)}


def test_whether_it_leaves_this_mac_never_changes(made: tuple[TraceSink, Held]) -> None:
    sink, _ = made
    first = sink.leaves_this_mac
    assert run(sink.export([ROOT])) is None

    assert sink.leaves_this_mac == first
    assert not first
