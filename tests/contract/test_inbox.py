"""The promises of the inbox port, checked on every inbox."""

import asyncio
import dataclasses
from collections.abc import Coroutine
from typing import Any

import pytest

from lamplighter.ports import Accepted, Failure, Inbound, Inbox, InboxEntry
from tests.contract.stores import MAX_RECORD_BYTES, Stores
from tests.store_samples import inbound


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def accepted(result: Accepted | Failure) -> Accepted:
    assert isinstance(result, Accepted), result
    return result


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


def pending(inbox: Inbox) -> tuple[InboxEntry, ...]:
    entries = run(inbox.pending())
    assert not isinstance(entries, Failure), entries
    return entries


@pytest.fixture
def inbox(stores: Stores) -> Inbox:
    return stores.inbox()


def test_an_accepted_message_is_pending_with_every_field(inbox: Inbox) -> None:
    first = accepted(run(inbox.accept(inbound())))
    assert first.is_new
    assert first.inbox_id >= 1
    assert pending(inbox) == (InboxEntry(inbox_id=first.inbox_id, inbound=inbound()),)


def test_a_second_accept_of_one_message_writes_nothing_and_keeps_the_first(
    inbox: Inbox,
) -> None:
    first = accepted(run(inbox.accept(inbound())))
    later_copy = dataclasses.replace(inbound(), text="an edited copy")
    second = accepted(run(inbox.accept(later_copy)))
    assert second == Accepted(inbox_id=first.inbox_id, is_new=False)
    assert [e.inbound.text for e in pending(inbox)] == [inbound().text]


def test_one_native_id_on_two_channels_is_two_messages(inbox: Inbox) -> None:
    run(inbox.accept(inbound("8812", channel="telegram")))
    assert accepted(run(inbox.accept(inbound("8812", channel="terminal")))).is_new


def test_pending_gives_messages_in_the_order_they_arrived(inbox: Inbox) -> None:
    for native_id in ("3", "1", "2"):
        run(inbox.accept(inbound(native_id)))
    assert [e.inbound.native_id for e in pending(inbox)] == ["3", "1", "2"]


def test_a_routed_message_is_no_longer_pending(inbox: Inbox) -> None:
    first = accepted(run(inbox.accept(inbound("1"))))
    run(inbox.accept(inbound("2")))
    assert run(inbox.mark_routed(first.inbox_id, "sess-a", 4)) is None
    assert [e.inbound.native_id for e in pending(inbox)] == ["2"]


def test_routing_again_to_the_same_place_changes_nothing(inbox: Inbox) -> None:
    first = accepted(run(inbox.accept(inbound())))
    run(inbox.mark_routed(first.inbox_id, "sess-a", 4))
    assert run(inbox.mark_routed(first.inbox_id, "sess-a", 4)) is None


@pytest.mark.parametrize(("session_id", "seq"), [("sess-b", 4), ("sess-a", 5)])
def test_routing_again_to_another_place_is_a_conflict(
    inbox: Inbox, session_id: str, seq: int
) -> None:
    first = accepted(run(inbox.accept(inbound())))
    run(inbox.mark_routed(first.inbox_id, "sess-a", 4))
    failure = failure_of(run(inbox.mark_routed(first.inbox_id, session_id, seq)))
    assert (failure.kind, failure.effect) == ("conflict", "none")


@pytest.mark.parametrize("inbox_id", [0, -1, 99])
def test_an_inbox_id_never_given_out_is_refused(inbox: Inbox, inbox_id: int) -> None:
    run(inbox.accept(inbound()))
    failure = failure_of(run(inbox.mark_routed(inbox_id, "sess-a", 1)))
    assert failure.kind == "invalid_input"


def test_a_routing_that_breaks_a_rule_is_refused(inbox: Inbox) -> None:
    first = accepted(run(inbox.accept(inbound())))
    for session_id, seq in (("-sess", 1), ("sess-a", 0)):
        failure = failure_of(run(inbox.mark_routed(first.inbox_id, session_id, seq)))
        assert failure.kind == "invalid_input"
    assert len(pending(inbox)) == 1


def test_a_message_over_the_size_limit_is_too_large(inbox: Inbox) -> None:
    big = dataclasses.replace(inbound(), text="x" * MAX_RECORD_BYTES)
    failure = failure_of(run(inbox.accept(big)))
    assert (failure.kind, failure.effect) == ("too_large", "none")
    assert pending(inbox) == ()


BROKEN = {
    "a channel with a capital": dataclasses.replace(inbound(), channel="Telegram"),
    "a channel that is too long": dataclasses.replace(inbound(), channel="t" * 33),
    "an empty native id": dataclasses.replace(inbound(), native_id=""),
    "a native id that is too long": dataclasses.replace(inbound(), native_id="1" * 257),
    "an empty conversation": dataclasses.replace(inbound(), conversation=""),
    "a time with no milliseconds": dataclasses.replace(
        inbound(), sent_at="2026-11-26T07:35:10Z"
    ),
    "a command with no name": dataclasses.replace(inbound(), kind="command"),
    "a message with a command name": dataclasses.replace(inbound(), command="stop"),
    "an answer with no question": dataclasses.replace(
        inbound(), kind="approval_answer", answer="yes"
    ),
    "a message with an answer": dataclasses.replace(inbound(), answer="no"),
    "an attachment with no hash": dataclasses.replace(
        inbound(),
        attachments=(dataclasses.replace(inbound().attachments[0], sha256="photo"),),
    ),
    "text that is no JSON": dataclasses.replace(inbound(), text="\ud800"),
    "v zero": dataclasses.replace(inbound(), v=0),
}


@pytest.mark.parametrize("broken", BROKEN.values(), ids=BROKEN.keys())
def test_a_message_that_breaks_a_rule_is_refused(inbox: Inbox, broken: Inbound) -> None:
    failure = failure_of(run(inbox.accept(broken)))
    assert (failure.kind, failure.effect) == ("invalid_input", "none")
    assert pending(inbox) == ()


def test_a_command_and_an_approval_answer_are_accepted(inbox: Inbox) -> None:
    command = dataclasses.replace(inbound("1"), kind="command", text="", command="stop")
    answer = dataclasses.replace(
        inbound("2"), kind="approval_answer", approval_id="ask-3", answer="yes"
    )
    run(inbox.accept(command))
    run(inbox.accept(answer))
    assert [e.inbound for e in pending(inbox)] == [command, answer]
