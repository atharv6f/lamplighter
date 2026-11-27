"""The Phoenix sink against a stand-in server: what it sends, asks and answers."""

import asyncio
import dataclasses
import json
import socket
from collections.abc import Callable, Sequence

import httpx
import pytest

from lamplighter.adapters.phoenix import PhoenixSink
from lamplighter.ports import Failure, Span
from tests.fake_clock import FakeClock
from tests.phoenix_stand_in import StandIn, stand_in

TRACE = "0e9989490d36bd20896dd8df2ee81290"
MARKER = "secret marker from the server"


def made_span(number: int) -> Span:
    return Span(
        trace_id=TRACE,
        span_id=f"{number:016x}",
        parent_id=None if number == 0 else f"{0:016x}",
        name=f"step {number}",
        kind="run" if number == 0 else "step",
        start_ms=1_795_678_512_100 + number,
        duration_ms=5,
        error=None,
        attributes={"lamp.step": number},
    )


BATCH = [made_span(number) for number in range(3)]


@dataclasses.dataclass
class Sent:
    result: Failure | None
    clock: FakeClock


def export(
    url: str,
    spans: Sequence[Span] = BATCH,
    project: str = "lamplighter",
    limit_s: float = 10.0,
) -> Sent:
    clock = FakeClock()

    async def send() -> Failure | None:
        async with httpx.AsyncClient(trust_env=False) as http:
            sink = PhoenixSink(url, project, http, clock, request_limit_s=limit_s)
            return await sink.export(spans)

    return Sent(asyncio.run(send()), clock)


def failure_of(sent: Sent) -> Failure:
    assert isinstance(sent.result, Failure), sent.result
    return sent.result


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_send_is_one_post_of_protobuf_that_decodes_to_the_batch() -> None:
    with stand_in() as server:
        sent = export(server.url)
        [post] = server.posts()
        [request] = server.requests

    assert sent.result is None
    assert post.path == "/v1/traces"
    assert post.headers["content-type"] == "application/x-protobuf"
    assert "content-encoding" not in post.headers
    resource = {
        kept.key: kept.value.string_value
        for kept in request.resource_spans[0].resource.attributes
    }
    assert resource == {
        "service.name": "lamplighter",
        "openinference.project.name": "lamplighter",
    }
    [scope] = request.resource_spans[0].scope_spans
    assert [span.span_id.hex() for span in scope.spans] == [s.span_id for s in BATCH]


def test_the_questions_name_the_project_and_at_most_100_ids_each() -> None:
    spans = [made_span(number) for number in range(230)]
    with stand_in() as server:
        sent = export(server.url, spans)
        gets = server.gets()

    assert sent.result is None
    assert [get.path for get in gets] == ["/v1/projects/lamplighter/spans"] * 3
    assert [len(get.query["span_id"]) for get in gets] == [100, 100, 30]
    assert [get.query["limit"] for get in gets] == [["100"]] * 3
    assert [i for get in gets for i in get.query["span_id"]] == [
        s.span_id for s in spans
    ]


def test_spans_never_shown_end_in_a_timeout_after_30_seconds() -> None:
    with stand_in() as server:
        server.script.never_shown = {BATCH[1].span_id, BATCH[2].span_id}
        sent = export(server.url)
        asked = [get.query["span_id"] for get in server.gets()]
    failed = failure_of(sent)

    assert (failed.kind, failed.effect) == ("timeout", "unknown")
    assert failed.message.startswith(
        "The trace store took the batch but does not show 2 of its spans in"
        " project lamplighter."
    )
    assert sent.clock.slept[:7] == [0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0]
    assert 30 <= sum(sent.clock.slept) < 31
    assert asked[0] == [span.span_id for span in BATCH]
    assert all(ids == [BATCH[1].span_id, BATCH[2].span_id] for ids in asked[1:])


def test_spans_that_show_up_on_the_third_question_are_confirmed() -> None:
    first = {"data": [{"context": {"span_id": BATCH[0].span_id}}]}
    with stand_in() as server:
        server.script.get_bodies = [json.dumps(first).encode(), b'{"data": []}']
        sent = export(server.url)
        asked = [get.query["span_id"] for get in server.gets()]

    assert sent.result is None
    assert sent.clock.slept == [0.05, 0.1]
    assert asked[1] == asked[2] == [BATCH[1].span_id, BATCH[2].span_id]


def test_a_project_that_is_not_there_yet_means_none_is_stored_yet() -> None:
    with stand_in() as server:
        server.script.hidden_for = 2
        sent = export(server.url)

    assert sent.result is None
    assert sent.clock.slept == [0.05, 0.1]


@pytest.mark.parametrize(
    ("status", "kind", "effect"),
    [
        (429, "unavailable", "unknown"),
        (502, "unavailable", "unknown"),
        (503, "unavailable", "unknown"),
        (504, "unavailable", "unknown"),
        (400, "invalid_input", "none"),
        (415, "invalid_input", "none"),
        (422, "invalid_input", "none"),
        (500, "fatal", "unknown"),
        (201, "fatal", "unknown"),
    ],
)
def test_each_answer_to_the_send_gives_its_kind(
    status: int, kind: str, effect: str
) -> None:
    with stand_in() as server:
        server.script.post_statuses = [status]
        failed = failure_of(export(server.url))
        questions = len(server.gets())

    assert (failed.kind, failed.effect) == (kind, effect)
    assert failed.message == f"The trace store answered {status}."
    assert questions == 0


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_an_answer_to_a_question_but_200_or_404_may_pass(status: int) -> None:
    with stand_in() as server:
        server.script.get_statuses = [status]
        server.script.get_bodies = [MARKER.encode()]
        failed = failure_of(export(server.url))

    assert (failed.kind, failed.effect) == ("unavailable", "unknown")
    assert failed.message == f"The trace store answered {status}."


@pytest.mark.parametrize(
    "body",
    [
        MARKER.encode(),
        b"\xff\xfe",
        b'{"rows": []}',
        b'{"data": {}}',
        b'{"data": [{"span_id": "0000000000000000"}]}',
        b'{"data": [{"context": {"span_id": 7}}]}',
        b"[]",
    ],
)
def test_a_list_of_spans_that_cannot_be_read_is_fatal(body: bytes) -> None:
    with stand_in() as server:
        server.script.get_bodies = [body]
        failed = failure_of(export(server.url))

    assert (failed.kind, failed.effect) == ("fatal", "unknown")
    assert failed.message == "The trace store's list of spans cannot be read."


def test_no_trace_store_on_the_port_is_unavailable() -> None:
    url = f"http://127.0.0.1:{free_port()}"
    failed = failure_of(export(url))

    assert (failed.kind, failed.effect) == ("unavailable", "unknown")
    assert failed.message == f"The trace store cannot be reached at {url}."


def test_the_address_is_sent_to_without_its_last_slash() -> None:
    with stand_in() as server:
        sent = export(server.url + "/")
        paths = [asked.path for asked in server.asked]

    assert sent.result is None
    assert paths[0] == "/v1/traces"


@pytest.mark.parametrize("where", ["send", "question"])
def test_an_answer_that_never_comes_is_a_timeout(where: str) -> None:
    with stand_in() as server:
        if where == "send":
            server.script.hang_posts = True
        else:
            server.script.hang_gets = True
        failed = failure_of(export(server.url, limit_s=0.3))

    assert (failed.kind, failed.effect) == ("timeout", "unknown")
    assert failed.message == "The trace store did not answer in time."


def test_a_redirect_is_not_followed() -> None:
    with stand_in() as other, stand_in() as server:
        server.script.redirect_to = other.url
        failed = failure_of(export(server.url))
        reached = len(other.asked)

    assert failed.message == "The trace store answered 302."
    assert reached == 0


def test_an_empty_batch_sends_nothing() -> None:
    with stand_in() as server:
        sent = export(server.url, [])
        asked = len(server.asked)

    assert sent.result is None
    assert asked == 0


@pytest.mark.parametrize(
    "setting", ["http://localhost:6006", "https://127.0.0.1:6006", "http://127.0.0.1"]
)
def test_an_address_off_this_mac_is_denied_and_sent_nothing(setting: str) -> None:
    clock = FakeClock()

    async def send() -> tuple[bool, Failure | None]:
        async with httpx.AsyncClient(trust_env=False) as http:
            sink = PhoenixSink(setting, "lamplighter", http, clock)
            return sink.leaves_this_mac, await sink.export(BATCH)

    leaves, result = asyncio.run(send())
    assert leaves
    assert isinstance(result, Failure)
    assert (result.kind, result.effect) == ("denied", "none")
    assert result.message == (
        "The trace store's address is not on this Mac. Nothing was sent."
    )


def test_no_message_holds_text_the_server_sent() -> None:
    with stand_in() as server:
        server.script.post_statuses = [500]
        failed = failure_of(export(server.url))

    assert MARKER not in failed.message


async def cancelled_then_again(server: StandIn, hang: Callable[[], None]) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        sink = PhoenixSink(server.url, "lamplighter", http, FakeClock())
        hang()
        task = asyncio.create_task(sink.export(BATCH))
        for _ in range(200):
            if server.asked and (server.asked[-1].method == "GET") == (
                server.script.hang_gets
            ):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        server.script.hang_posts = server.script.hang_gets = False
        assert await sink.export(BATCH) is None


@pytest.mark.parametrize("where", ["send", "question"])
def test_a_cancel_is_passed_on_and_the_next_export_works(where: str) -> None:
    with stand_in() as server:

        def hang() -> None:
            if where == "send":
                server.script.hang_posts = True
            else:
                server.script.hang_gets = True

        asyncio.run(cancelled_then_again(server, hang))
        names = server.names()

    assert set(names) == {span.span_id for span in BATCH}
