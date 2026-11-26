"""The promises of the outbox port, checked on every outbox."""

import asyncio
import dataclasses
from collections.abc import Coroutine
from typing import Any

import pytest

from lamplighter.ports import (
    DeliveryReceipt,
    Failure,
    Outbound,
    Outbox,
    out_id_for,
)
from tests.contract.stores import MAX_RECORD_BYTES, Stores

RECEIPT = DeliveryReceipt(at="2026-11-26T07:35:13.000Z")
OTHER_RECEIPT = DeliveryReceipt(at="2026-11-26T07:35:14.000Z")
CLEAN = Failure(kind="unavailable", effect="none", message="Not there.", source="t")
MAYBE = Failure(kind="timeout", effect="unknown", message="No answer.", source="t")


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def reply(
    seq: int, text: str = "The kettle is on.", session: str = "sess-a"
) -> Outbound:
    return Outbound(
        out_id=out_id_for(session, seq), session_id=session, kind="reply", text=text
    )


def due(outbox: Outbox) -> tuple[Outbound, ...]:
    rows = run(outbox.due())
    assert not isinstance(rows, Failure), rows
    return rows


def ok(result: None | Failure) -> None:
    assert result is None, result


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


@pytest.fixture
def outbox(stores: Stores) -> Outbox:
    return stores.outbox()


def test_an_enqueued_row_is_due_with_every_field(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    assert due(outbox) == (reply(3),)


def test_enqueue_twice_is_one_row(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.enqueue(reply(3))))
    assert due(outbox) == (reply(3),)


def test_other_content_under_one_id_is_a_conflict(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    refused = failure_of(run(outbox.enqueue(reply(3, text="Another text."))))
    assert refused.kind == "conflict"
    assert due(outbox) == (reply(3),)


def test_due_gives_the_order_of_enqueue(outbox: Outbox) -> None:
    for seq in (9, 3, 6):
        ok(run(outbox.enqueue(reply(seq))))
    assert due(outbox) == (reply(9), reply(3), reply(6))


def test_due_gives_every_session_and_leaves_out_sent_rows(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.enqueue(reply(4, session="sess-b"))))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    assert due(outbox) == (reply(4, session="sess-b"),)


def test_a_row_started_and_never_marked_is_labelled(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.start_send(reply(3).out_id)))
    assert due(outbox) == (dataclasses.replace(reply(3), maybe_duplicate=True),)


def test_a_fresh_row_is_not_labelled_after_its_first_start(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_failed(reply(3).out_id, CLEAN)))
    assert due(outbox) == (reply(3),)


def test_a_row_started_twice_then_failed_cleanly_is_still_labelled(
    outbox: Outbox,
) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_failed(reply(3).out_id, CLEAN)))
    assert due(outbox) == (dataclasses.replace(reply(3), maybe_duplicate=True),)
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_failed(reply(3).out_id, CLEAN)))
    assert due(outbox)[0].maybe_duplicate


def test_a_failure_that_may_have_gone_out_labels_the_row(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_failed(reply(3).out_id, MAYBE)))
    assert due(outbox) == (dataclasses.replace(reply(3), maybe_duplicate=True),)
    ok(run(outbox.mark_failed(reply(3).out_id, CLEAN)))
    assert due(outbox)[0].maybe_duplicate


def test_mark_sent_twice_with_the_same_receipt_is_a_no_op(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.start_send(reply(3).out_id)))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    assert due(outbox) == ()


def test_mark_sent_with_another_receipt_after_sent_is_a_conflict(
    outbox: Outbox,
) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    assert failure_of(run(outbox.mark_sent(reply(3).out_id, OTHER_RECEIPT))).kind == (
        "conflict"
    )


def test_a_pending_row_can_be_marked_sent(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    assert due(outbox) == ()


def test_start_send_and_mark_failed_on_a_sent_row_are_conflicts(
    outbox: Outbox,
) -> None:
    ok(run(outbox.enqueue(reply(3))))
    ok(run(outbox.mark_sent(reply(3).out_id, RECEIPT)))
    assert failure_of(run(outbox.start_send(reply(3).out_id))).kind == "conflict"
    assert failure_of(run(outbox.mark_failed(reply(3).out_id, CLEAN))).kind == (
        "conflict"
    )
    assert due(outbox) == ()


def test_an_id_the_outbox_never_saw_is_invalid_input(outbox: Outbox) -> None:
    unknown = reply(99).out_id
    assert failure_of(run(outbox.start_send(unknown))).kind == "invalid_input"
    assert failure_of(run(outbox.mark_sent(unknown, RECEIPT))).kind == "invalid_input"
    assert failure_of(run(outbox.mark_failed(unknown, CLEAN))).kind == "invalid_input"


def test_a_message_over_the_size_limit_is_too_large(outbox: Outbox) -> None:
    big = reply(3, text="x" * MAX_RECORD_BYTES)
    assert failure_of(run(outbox.enqueue(big))).kind == "too_large"
    assert due(outbox) == ()


@pytest.mark.parametrize(
    "bad",
    [
        dataclasses.replace(reply(3), out_id="not-a-hash"),
        dataclasses.replace(reply(3), session_id="-bad"),
        dataclasses.replace(reply(3), maybe_duplicate=True),
        dataclasses.replace(reply(3), v=0),
        dataclasses.replace(reply(3), text="\ud800"),
    ],
    ids=["out_id", "session", "already a repeat", "version", "lone surrogate"],
)
def test_a_message_that_breaks_a_rule_is_invalid_input(
    outbox: Outbox, bad: Outbound
) -> None:
    assert failure_of(run(outbox.enqueue(bad))).kind == "invalid_input"
    assert due(outbox) == ()


def test_a_receipt_with_a_bad_time_is_invalid_input(outbox: Outbox) -> None:
    ok(run(outbox.enqueue(reply(3))))
    bad = DeliveryReceipt(at="yesterday")
    assert failure_of(run(outbox.mark_sent(reply(3).out_id, bad))).kind == (
        "invalid_input"
    )
    assert due(outbox) == (reply(3),)
