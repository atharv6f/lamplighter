"""The promises of the event log port, checked on every log that implements it."""

import asyncio
import dataclasses
from collections.abc import Coroutine
from typing import Any

import pytest

from lamplighter.ports import (
    Event,
    EventLog,
    Failure,
    JsonObject,
    NewEvent,
    event_id_for,
    inbound_event_id,
)
from tests.contract.stores import MAX_RECORD_BYTES, Stores

AT = "2026-11-26T07:35:12.345Z"


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def note(text: str = "the kettle is on", **fields: Any) -> NewEvent:
    return NewEvent(type="note", v=1, payload={"text": text}, at=AT, **fields)


def user_message(native_id: str) -> NewEvent:
    return NewEvent(
        type="user_message",
        v=1,
        payload={"text": "What is on my list?"},
        at=AT,
        event_id=inbound_event_id("terminal", native_id),
    )


def run_mark(kind: str) -> NewEvent:
    return NewEvent(type=kind, v=1, payload={}, at=AT, run_id="run-1")


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


def events_of(result: tuple[Event, ...] | Failure) -> tuple[Event, ...]:
    assert not isinstance(result, Failure), result
    return result


def texts(events: tuple[Event, ...]) -> list[object]:
    return [event.payload.get("text") for event in events]


@pytest.fixture
def log(stores: Stores) -> EventLog:
    return stores.log({})


def test_a_batch_reads_back_in_order_and_the_last_seq_is_returned(
    log: EventLog,
) -> None:
    assert run(log.append("sess-a", [note("one"), note("two")], 0)) == 2
    assert run(log.append("sess-a", [note("three")], 2)) == 3
    events = events_of(run(log.read("sess-a", 0)))
    assert [event.seq for event in events] == [1, 2, 3]
    assert texts(events) == ["one", "two", "three"]


def test_every_field_of_an_event_is_saved(log: EventLog) -> None:
    cause = event_id_for("sess-a", 1)
    new = note("tea", run_id="run-1", step=2, duration_ms=0, cause=cause)
    run(log.append("sess-a", [note("first"), new], 0))
    saved = events_of(run(log.read("sess-a", 1)))[0]
    assert saved.session_id == "sess-a"
    assert saved.seq == 2
    assert saved.type == "note"
    assert saved.v == 1
    assert saved.payload == {"text": "tea"}
    assert saved.at == AT
    assert (saved.run_id, saved.step, saved.duration_ms) == ("run-1", 2, 0)
    assert saved.cause == cause


def test_an_event_id_is_worked_out_from_its_place(log: EventLog) -> None:
    run(log.append("sess-a", [note(), note()], 0))
    events = events_of(run(log.read("sess-a", 0)))
    assert [e.event_id for e in events] == [
        event_id_for("sess-a", 1),
        event_id_for("sess-a", 2),
    ]


def test_a_user_message_keeps_the_id_it_carries(log: EventLog) -> None:
    run(log.append("sess-a", [user_message("8812")], 0))
    saved = events_of(run(log.read("sess-a", 0)))[0]
    assert saved.event_id == inbound_event_id("terminal", "8812")


def test_a_read_starts_after_the_seq_it_is_given(log: EventLog) -> None:
    run(log.append("sess-a", [note("one"), note("two"), note("three")], 0))
    assert texts(events_of(run(log.read("sess-a", 1)))) == ["two", "three"]
    assert events_of(run(log.read("sess-a", 3))) == ()
    assert events_of(run(log.read("sess-a", 9))) == ()


def test_a_session_with_no_events_reads_as_empty(log: EventLog) -> None:
    assert events_of(run(log.read("nobody", 0))) == ()


@pytest.mark.parametrize("expected_seq", [0, 1, 3])
def test_an_append_that_is_not_at_the_end_is_a_conflict_and_writes_nothing(
    log: EventLog, expected_seq: int
) -> None:
    run(log.append("sess-a", [note("one"), note("two")], 0))
    failure = failure_of(run(log.append("sess-a", [note("late")], expected_seq)))
    assert (failure.kind, failure.effect) == ("conflict", "none")
    assert texts(events_of(run(log.read("sess-a", 0)))) == ["one", "two"]


def test_of_two_appends_with_one_expected_seq_exactly_one_wins(log: EventLog) -> None:
    run(log.append("sess-a", [note("one")], 0))
    first = run(log.append("sess-a", [note("two")], 1))
    second = run(log.append("sess-a", [note("other two")], 1))
    assert first == 2
    assert failure_of(second).kind == "conflict"
    assert texts(events_of(run(log.read("sess-a", 0)))) == ["one", "two"]


def test_a_new_session_starts_at_zero(log: EventLog) -> None:
    assert failure_of(run(log.append("sess-a", [note()], 1))).kind == "conflict"
    assert run(log.append("sess-a", [note()], 0)) == 1


def test_a_repeated_user_message_is_a_conflict_in_the_same_session(
    log: EventLog,
) -> None:
    run(log.append("sess-a", [user_message("8812")], 0))
    failure = failure_of(run(log.append("sess-a", [note(), user_message("8812")], 1)))
    assert failure.kind == "conflict"
    assert len(events_of(run(log.read("sess-a", 0)))) == 1


def test_a_repeated_user_message_is_a_conflict_in_another_session(
    log: EventLog,
) -> None:
    run(log.append("sess-a", [user_message("8812")], 0))
    failure = failure_of(run(log.append("sess-b", [user_message("8812")], 0)))
    assert failure.kind == "conflict"
    assert events_of(run(log.read("sess-b", 0))) == ()


def test_one_user_message_twice_in_one_batch_is_a_conflict(log: EventLog) -> None:
    batch = [user_message("8812"), user_message("8812")]
    assert failure_of(run(log.append("sess-a", batch, 0))).kind == "conflict"
    assert events_of(run(log.read("sess-a", 0))) == ()


def test_tail_walks_every_event_once_in_commit_order(log: EventLog) -> None:
    run(log.append("sess-b", [note("b1"), note("b2")], 0))
    run(log.append("sess-a", [note("a1")], 0))
    run(log.append("sess-b", [note("b3")], 2))
    seen: list[Event] = []
    after = 0
    while page := events_of(run(log.tail(after, 2))):
        seen.extend(page)
        after = page[-1].pos
    assert texts(tuple(seen)) == ["b1", "b2", "a1", "b3"]
    positions = [event.pos for event in seen]
    assert positions == sorted(set(positions))


def test_tail_gives_no_more_than_the_limit(log: EventLog) -> None:
    run(log.append("sess-a", [note("one"), note("two"), note("three")], 0))
    assert texts(events_of(run(log.tail(0, 2)))) == ["one", "two"]


def test_an_event_of_a_type_no_code_knows_comes_back_unchanged(log: EventLog) -> None:
    odd = NewEvent(
        type="from_the_future",
        v=7,
        payload={"nested": {"list": [1, 2.5, None, True]}, "text": "café"},
        at=AT,
    )
    run(log.append("sess-a", [odd], 0))
    saved = events_of(run(log.read("sess-a", 0)))[0]
    assert (saved.type, saved.v) == ("from_the_future", 7)
    assert saved.payload == odd.payload


def _rename_name_to_title(payload: JsonObject) -> JsonObject:
    return {"title": payload["name"]}


def _add_a_colour(payload: JsonObject) -> JsonObject:
    return {**payload, "colour": "blue"}


def test_an_old_event_reads_in_its_newest_form(stores: Stores) -> None:
    upcasters = {("made_up", 1): _rename_name_to_title, ("made_up", 2): _add_a_colour}
    log = stores.log(upcasters)
    old = NewEvent(type="made_up", v=1, payload={"name": "tea"}, at=AT)
    run(log.append("sess-a", [old, note()], 0))
    newest = {"title": "tea", "colour": "blue"}
    read_back = events_of(run(log.read("sess-a", 0)))[0]
    tailed = events_of(run(log.tail(0, 1)))[0]
    for event in (read_back, tailed):
        assert (event.v, event.payload) == (3, newest)


def test_an_empty_batch_is_refused(log: EventLog) -> None:
    assert failure_of(run(log.append("sess-a", [], 0))).kind == "invalid_input"


BROKEN = {
    "a type with a capital": dataclasses.replace(note(), type="Note"),
    "a type with a dash": dataclasses.replace(note(), type="a-note"),
    "a type that is too long": dataclasses.replace(note(), type="n" * 65),
    "v zero": dataclasses.replace(note(), v=0),
    "a time with no milliseconds": dataclasses.replace(
        note(), at="2026-11-26T07:35:12Z"
    ),
    "a time that is no date": dataclasses.replace(
        note(), at="2026-13-26T07:35:12.345Z"
    ),
    "a run id with a space": note(run_id="run 1"),
    "step zero": note(step=0),
    "a negative duration": note(duration_ms=-1),
    "a cause that is no hash": note(cause="event-3"),
    "a user message with no id": dataclasses.replace(user_message("1"), event_id=None),
    "a user message whose id is no hash": dataclasses.replace(
        user_message("1"), event_id="8812"
    ),
    "another type that carries an id": note(event_id=event_id_for("sess-a", 1)),
    "a payload that is no JSON": note(text="\ud800"),
}


@pytest.mark.parametrize("broken", BROKEN.values(), ids=BROKEN.keys())
def test_a_batch_with_one_broken_event_is_refused_whole(
    log: EventLog, broken: NewEvent
) -> None:
    failure = failure_of(run(log.append("sess-a", [note(), broken], 0)))
    assert (failure.kind, failure.effect) == ("invalid_input", "none")
    assert events_of(run(log.read("sess-a", 0))) == ()


@pytest.mark.parametrize(
    "session_id", ["", "-sess", "_sess", "sess a", "sess/a", "s" * 65, "séss"]
)
def test_a_session_id_that_breaks_the_rule_is_refused(
    log: EventLog, session_id: str
) -> None:
    assert failure_of(run(log.append(session_id, [note()], 0))).kind == "invalid_input"
    assert failure_of(run(log.read(session_id, 0))).kind == "invalid_input"


def test_the_longest_session_id_is_accepted(log: EventLog) -> None:
    assert run(log.append("S" + "s_-9" * 15 + "abc", [note()], 0)) == 1


def test_negative_numbers_are_refused(log: EventLog) -> None:
    assert failure_of(run(log.append("sess-a", [note()], -1))).kind == "invalid_input"
    assert failure_of(run(log.read("sess-a", -1))).kind == "invalid_input"
    assert failure_of(run(log.tail(-1, 5))).kind == "invalid_input"
    assert failure_of(run(log.tail(0, 0))).kind == "invalid_input"


def test_a_payload_over_the_limit_is_too_large_and_refuses_the_batch(
    log: EventLog,
) -> None:
    big = note("x" * MAX_RECORD_BYTES)
    failure = failure_of(run(log.append("sess-a", [note(), big], 0)))
    assert (failure.kind, failure.effect) == ("too_large", "none")
    assert events_of(run(log.read("sess-a", 0))) == ()


def test_a_payload_at_the_limit_is_accepted(log: EventLog) -> None:
    overhead = len('{"text":""}')
    assert run(log.append("sess-a", [note("x" * (MAX_RECORD_BYTES - overhead))], 0))


def test_a_broken_rule_is_reported_before_a_size_or_a_stale_place(
    log: EventLog,
) -> None:
    run(log.append("sess-a", [note()], 0))
    big = note("x" * MAX_RECORD_BYTES)
    broken = note(step=0)
    assert failure_of(run(log.append("sess-a", [big, broken], 0))).kind == (
        "invalid_input"
    )
    assert failure_of(run(log.append("sess-a", [big], 0))).kind == "too_large"


def test_sessions_are_listed_by_id_with_their_last_event(log: EventLog) -> None:
    run(log.append("sess-b", [note()], 0))
    later = dataclasses.replace(note(), at="2026-11-26T08:00:00.000Z")
    run(log.append("sess-a", [note(), later], 0))
    infos = run(log.sessions(None))
    assert not isinstance(infos, Failure)
    assert [(i.session_id, i.last_seq, i.last_at) for i in infos] == [
        ("sess-a", 2, "2026-11-26T08:00:00.000Z"),
        ("sess-b", 1, AT),
    ]


def test_a_session_is_unfinished_while_its_last_run_has_not_ended(
    log: EventLog,
) -> None:
    run(log.append("open", [note(), run_mark("run_started"), note()], 0))
    run(log.append("closed", [run_mark("run_started"), run_mark("run_end")], 0))
    run(log.append("again", [run_mark("run_started"), run_mark("run_end")], 0))
    run(log.append("again", [run_mark("run_started")], 2))
    run(log.append("quiet", [note()], 0))
    infos = run(log.sessions(None))
    assert not isinstance(infos, Failure)
    assert {i.session_id: i.state for i in infos} == {
        "again": "unfinished",
        "closed": "idle",
        "open": "unfinished",
        "quiet": "idle",
    }
    unfinished = run(log.sessions("unfinished"))
    idle = run(log.sessions("idle"))
    assert not isinstance(unfinished, Failure)
    assert not isinstance(idle, Failure)
    assert [i.session_id for i in unfinished] == ["again", "open"]
    assert [i.session_id for i in idle] == ["closed", "quiet"]


def test_an_empty_log_has_no_sessions(log: EventLog) -> None:
    assert run(log.sessions(None)) == ()
