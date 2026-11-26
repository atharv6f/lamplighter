"""The driver and the intake together, as the chat process runs them."""

import asyncio
from dataclasses import replace

from lamplighter.adapters import CountingIds, ScriptedChannel
from lamplighter.core import (
    Deps,
    RunningSessions,
    TurnEnd,
    Wake,
    deliver_due,
    drive,
    run_turn,
    take,
)
from lamplighter.ports import Failure, Outbound, out_id_for
from tests.chat_kit import (
    Stores,
    chat,
    first_and_second,
    outcome,
    process,
)
from tests.port_counter import Counter
from tests.run_history import SESSION, SPEC, inbound
from tests.test_run_turn import FailingLog
from tests.waiting_clock import JumpingClock


def test_each_message_is_answered_and_the_driver_leaves_when_input_ends() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        channel = ScriptedChannel(first_and_second(), clock, one_at_a_time=True)
        made = process(stores, channel, clock, CountingIds(), Counter())
        assert await chat(made, SPEC) is None
        result = await outcome(stores, channel)
        assert result.run_ends == ["done", "done"]
        assert [out.text for out in result.sent] == ["First answer.", "Second answer."]
        assert result.pending == () and result.due == ()
        assert channel.acked == ["m1", "m2"]

    asyncio.run(scenario())


def test_a_failed_send_ends_the_driver_with_its_failure_and_the_reply_waits() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        channel = ScriptedChannel(
            first_and_second(), clock, one_at_a_time=True, fail_first_send=True
        )
        made = process(stores, channel, clock, CountingIds(), Counter())
        failed = await chat(made, SPEC)
        assert isinstance(failed, Failure), failed
        result = await outcome(stores, channel)
        # The reply stays in the outbox, for the next start to send again.
        assert [out.text for out in result.due] == ["First answer."]

    asyncio.run(scenario())


def test_the_first_trip_sends_a_reply_a_kill_left_unsent() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        left = Outbound(
            out_id=out_id_for(SESSION, 9),
            session_id=SESSION,
            kind="reply",
            text="From before.",
        )
        assert await stores.outbox.enqueue(left) is None
        assert await stores.outbox.start_send(left.out_id) is None
        channel = ScriptedChannel([], clock)
        made = process(stores, channel, clock, CountingIds(), Counter())
        assert await chat(made, SPEC) is None
        assert channel.sent == [replace(left, maybe_duplicate=True)]

    asyncio.run(scenario())


def test_a_run_cut_off_by_a_kill_is_carried_on_with_no_new_input() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        ids = CountingIds()
        first = process(stores, ScriptedChannel([], clock), clock, ids, Counter())
        await stores.inbox.accept(inbound("m1", "Note one and tick."))
        # The fifth append fails as a kill would, in the middle of the run.
        deps = replace(first.deps, log=FailingLog(stores.log, failing=5))
        assert isinstance(await run_turn(SESSION, SPEC, deps), Failure)
        channel = ScriptedChannel([], clock)
        again = process(stores, channel, clock, ids, Counter())
        assert await chat(again, SPEC) is None
        result = await outcome(stores, channel)
        assert result.run_ends == ["done"]
        assert [out.text for out in result.sent] == ["First answer."]

    asyncio.run(scenario())


def test_a_reply_a_kill_left_unsent_goes_out_before_the_cut_off_run_goes_on() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        ids = CountingIds()
        left = Outbound(
            out_id=out_id_for(SESSION, 900),
            session_id=SESSION,
            kind="reply",
            text="From before.",
        )
        assert await stores.outbox.enqueue(left) is None
        first = process(stores, ScriptedChannel([], clock), clock, ids, Counter())
        await stores.inbox.accept(inbound("m1", "Note one and tick."))
        deps = replace(first.deps, log=FailingLog(stores.log, failing=5))
        assert isinstance(await run_turn(SESSION, SPEC, deps), Failure)
        counter = Counter()
        channel = ScriptedChannel([], clock)
        again = process(stores, channel, clock, ids, counter)
        assert await chat(again, SPEC) is None
        assert [out.text for out in channel.sent] == ["From before.", "First answer."]
        assert counter.names.index("channel.send") < counter.names.index("model.stream")

    asyncio.run(scenario())


def test_a_driver_with_nothing_to_do_shows_the_prompt_and_waits() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        made = process(
            stores, ScriptedChannel([], clock), clock, CountingIds(), Counter()
        )
        wake = Wake()
        prompts: list[bool] = []
        driving = asyncio.ensure_future(
            drive(
                SESSION,
                SPEC,
                made.deps,
                made.channel,
                wake,
                lambda: prompts.append(True),
            )
        )
        await asyncio.sleep(0)
        assert prompts == [True] and not driving.done()
        await take(
            inbound("m1"), stores.inbox, made.channel, made.deps.running, wake.wake
        )
        await asyncio.sleep(0)
        for _ in range(200):
            if len(prompts) == 2:
                break
            await asyncio.sleep(0)
        assert len(prompts) == 2
        wake.end_input()
        assert await driving is None

    asyncio.run(scenario())


def test_with_all_three_switches_on_every_message_is_answered_once() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        stores = Stores()
        messages = [
            inbound("a1", "Note one.", session="sess-a"),
            inbound("b1", "Note one.", session="sess-b"),
            inbound("a2", "Again.", session="sess-a"),
            inbound("c1", "Hello.", session="sess-c"),
        ]
        channel = ScriptedChannel(
            messages,
            clock,
            repeat_first=True,
            out_of_order=True,
            fail_first_send=True,
        )
        made = process(stores, channel, clock, CountingIds(), Counter())
        deps: Deps = made.deps
        running = RunningSessions()
        async for arrived in channel.receive():
            await take(arrived, deps.inbox, channel, running, lambda: None)
        conversations = ["sess-a", "sess-b", "sess-c"]
        for _ in range(10):
            for conversation in conversations:
                while isinstance(await run_turn(conversation, SPEC, deps), TurnEnd):
                    pass
                await deliver_due(conversation, deps.outbox, channel)
        assert await stores.inbox.pending() == ()
        assert await stores.outbox.due() == ()
        for conversation in conversations:
            events = await stores.log.read(conversation, 0)
            assert not isinstance(events, Failure)
            said = [e.event_id for e in events if e.type == "user_message"]
            assert len(said) == len(set(said))
            assert all(
                e.payload["reason"] == "done" for e in events if e.type == "run_end"
            )
        user_messages = 0
        for conversation in conversations:
            events = await stores.log.read(conversation, 0)
            assert not isinstance(events, Failure)
            user_messages += sum(1 for e in events if e.type == "user_message")
        assert user_messages == 4
        # The first send may have gone out, so its second copy says so, and
        # it is the only reply shown twice.
        first = channel.sent[0]
        copies = [out for out in channel.sent if out.out_id == first.out_id]
        assert [out.maybe_duplicate for out in copies] == [False, True]
        out_ids = [out.out_id for out in channel.sent]
        assert len(out_ids) == len(set(out_ids)) + 1

    asyncio.run(scenario())
