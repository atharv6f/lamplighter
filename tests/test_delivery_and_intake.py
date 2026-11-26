"""Sending what is due, and taking what arrives, each in its fixed order."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import replace

import pytest

from lamplighter.adapters import ScriptedChannel
from lamplighter.adapters.in_memory import InMemoryInbox, InMemoryOutbox
from lamplighter.core import RunningSessions, deliver_due, run_intake, take
from lamplighter.ports import (
    Accepted,
    DeliveryReceipt,
    Failure,
    Inbound,
    InboxEntry,
    Outbound,
    Sender,
    out_id_for,
)
from tests.fake_clock import FakeClock
from tests.run_history import inbound, stop

SESSION = "sess-a"


def reply(seq: int, session: str = SESSION, text: str = "Hi.") -> Outbound:
    return Outbound(
        out_id=out_id_for(session, seq), session_id=session, kind="reply", text=text
    )


async def enqueue(outbox: InMemoryOutbox, *rows: Outbound) -> None:
    for row in rows:
        assert await outbox.enqueue(row) is None


class FailingChannel(ScriptedChannel):
    """Fails every send while `down` is set, after nothing was shown."""

    def __init__(self) -> None:
        super().__init__([], FakeClock())
        self.down = True

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        if self.down:
            return Failure(kind="unavailable", effect="none", message="No.", source="t")
        return await super().send(out)


def test_only_the_sessions_own_rows_are_sent_in_order() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox(10_000)
        channel = ScriptedChannel([], FakeClock())
        await enqueue(outbox, reply(3), reply(5, "sess-b"), reply(7, text="Bye."))
        assert await deliver_due(SESSION, outbox, channel) is None
        assert [out.text for out in channel.sent] == ["Hi.", "Bye."]
        due = await outbox.due()
        assert not isinstance(due, Failure)
        assert [out.session_id for out in due] == ["sess-b"]

    asyncio.run(scenario())


def test_a_failed_send_stops_and_the_next_call_sends_in_order() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox(10_000)
        channel = FailingChannel()
        await enqueue(outbox, reply(3, text="First."), reply(7, text="Second."))
        assert await deliver_due(SESSION, outbox, channel) is None
        assert channel.sent == []
        channel.down = False
        assert await deliver_due(SESSION, outbox, channel) is None
        assert [out.text for out in channel.sent] == ["First.", "Second."]
        assert not any(out.maybe_duplicate for out in channel.sent)

    asyncio.run(scenario())


def test_a_send_cut_off_by_a_kill_is_sent_again_and_labelled() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox(10_000)
        channel = ScriptedChannel([], FakeClock())
        await enqueue(outbox, reply(3))
        # A kill after the send began and before it was marked.
        assert await outbox.start_send(out_id_for(SESSION, 3)) is None
        assert await deliver_due(SESSION, outbox, channel) is None
        assert [out.maybe_duplicate for out in channel.sent] == [True]
        assert await outbox.due() == ()

    asyncio.run(scenario())


def test_a_send_that_may_have_gone_out_is_labelled_the_next_time() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox(10_000)
        channel = ScriptedChannel([], FakeClock(), fail_first_send=True)
        await enqueue(outbox, reply(3), reply(7))
        await deliver_due(SESSION, outbox, channel)
        await deliver_due(SESSION, outbox, channel)
        assert [(out.out_id[:4], out.maybe_duplicate) for out in channel.sent] == [
            (out_id_for(SESSION, 3)[:4], False),
            (out_id_for(SESSION, 3)[:4], True),
            (out_id_for(SESSION, 7)[:4], False),
        ]

    asyncio.run(scenario())


class Killed(BaseException):
    """Stands for the process dying part way through a call."""


class DiesDuringFirstSend(ScriptedChannel):
    """Dies inside the first send, after the message may have been shown."""

    def __init__(self) -> None:
        super().__init__([], FakeClock())
        self.died = False

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        if not self.died:
            self.died = True
            raise Killed
        return await super().send(out)


def test_a_send_is_saved_as_begun_so_a_kill_inside_it_is_labelled() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox(10_000)
        channel = DiesDuringFirstSend()
        await enqueue(outbox, reply(3))
        with pytest.raises(Killed):
            await deliver_due(SESSION, outbox, channel)
        assert await deliver_due(SESSION, outbox, channel) is None
        assert [out.maybe_duplicate for out in channel.sent] == [True]

    asyncio.run(scenario())


class Recorder:
    """Wraps the inbox and the channel so the order of their calls is seen."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.inbox = InMemoryInbox(2_000)
        self.channel = ScriptedChannel([], FakeClock())
        self.running = RunningSessions()

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        self.order.append("accept")
        return await self.inbox.accept(inbound)

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        return await self.inbox.pending()

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        return await self.inbox.mark_routed(inbox_id, session_id, seq)

    def receive(self) -> AsyncGenerator[Inbound]:
        return self.channel.receive()

    async def ack(self, native_id: str) -> None:
        self.order.append(f"ack, stop flag {self.running.stop_asked(SESSION)}")
        await self.channel.ack(native_id)

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        return await self.channel.send(out)

    def wake(self) -> None:
        self.order.append("wake")

    async def take(self, made: Inbound) -> Failure | None:
        return await take(made, self, self, self.running, self.wake)


def test_a_message_is_saved_then_acked_then_the_driver_is_woken() -> None:
    async def scenario() -> None:
        seen = Recorder()
        assert await seen.take(inbound("n1")) is None
        assert seen.order == ["accept", "ack, stop flag False", "wake"]
        assert not seen.running.stop_asked(SESSION)

    asyncio.run(scenario())


def test_a_stop_raises_its_flag_before_the_ack() -> None:
    async def scenario() -> None:
        seen = Recorder()
        assert await seen.take(stop("s1")) is None
        assert seen.order == ["accept", "ack, stop flag True", "wake"]
        assert not seen.running.stop_asked("sess-b")

    asyncio.run(scenario())


def test_a_stop_given_again_raises_no_flag() -> None:
    async def scenario() -> None:
        seen = Recorder()
        await seen.take(stop("s1"))
        seen.running.clear_stop(SESSION)
        await seen.take(stop("s1"))
        assert not seen.running.stop_asked(SESSION)
        rows = await seen.inbox.pending()
        assert not isinstance(rows, Failure) and len(rows) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "made",
    [
        replace(stop("c1"), command="status", text="status"),
        replace(
            inbound("a1"), kind="approval_answer", approval_id="ap-1", answer="yes"
        ),
        replace(inbound("u1"), sender=Sender(id="stranger", verified=False)),
        replace(stop("u2"), sender=Sender(id="stranger", verified=False)),
    ],
)
def test_what_may_not_come_in_is_acked_and_dropped(
    made: Inbound, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> Recorder:
        seen = Recorder()
        assert await seen.take(made) is None
        return seen

    with caplog.at_level(logging.WARNING):
        seen = asyncio.run(scenario())
    assert seen.order == ["ack, stop flag False"]
    assert seen.channel.acked == [made.native_id]
    assert asyncio.run(seen.inbox.pending()) == ()
    assert "dropped" in caplog.text


def test_a_message_too_large_to_save_is_acked_and_dropped() -> None:
    async def scenario() -> Recorder:
        seen = Recorder()
        assert await seen.take(inbound("n1", text="x" * 5_000)) is None
        return seen

    seen = asyncio.run(scenario())
    assert seen.order == ["accept", "ack, stop flag False"]


class BrokenInbox(InMemoryInbox):
    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        return Failure(kind="unavailable", effect="none", message="Gone.", source="t")


def test_any_other_inbox_failure_stops_the_intake_without_an_ack() -> None:
    async def scenario() -> None:
        channel = ScriptedChannel([inbound("n1"), inbound("n2")], FakeClock())
        ended: list[bool] = []
        woken: list[bool] = []
        failed = await run_intake(
            channel,
            BrokenInbox(2_000),
            RunningSessions(),
            lambda: woken.append(True),
            lambda: ended.append(True),
        )
        assert isinstance(failed, Failure) and failed.message == "Gone."
        assert channel.acked == []
        assert woken == []
        assert ended == [True]

    asyncio.run(scenario())


def test_the_intake_takes_every_message_and_then_says_the_input_ended() -> None:
    async def scenario() -> None:
        channel = ScriptedChannel([inbound("n1"), stop("s1")], FakeClock())
        inbox = InMemoryInbox(2_000)
        events: list[str] = []
        failed = await run_intake(
            channel,
            inbox,
            RunningSessions(),
            lambda: events.append("wake"),
            lambda: events.append("ended"),
        )
        assert failed is None
        assert events == ["wake", "wake", "ended"]
        assert channel.acked == ["n1", "s1"]

    asyncio.run(scenario())
