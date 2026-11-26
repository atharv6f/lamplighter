"""The promises of the channel port, checked on every channel.

The promises about a message given again after a restart are checked on
the scripted channel only, because a terminal cannot give a typed line
again.
"""

import asyncio
from collections.abc import Callable, Sequence

import pytest

from lamplighter.adapters import ScriptedChannel
from lamplighter.adapters.in_memory import InMemoryInbox
from lamplighter.ports import (
    Accepted,
    Channel,
    DeliveryReceipt,
    Failure,
    Inbound,
    Outbound,
)
from tests.contract.channels import KINDS, Played
from tests.fake_clock import FakeClock
from tests.run_history import inbound

type Kind = Callable[[Sequence[str]], Played]


@pytest.fixture(params=sorted(KINDS))
def kind(request: pytest.FixtureRequest) -> Kind:
    return KINDS[request.param]


def note(text: str, seq: int = 3) -> Outbound:
    return Outbound(out_id=f"out-{seq}", session_id="sess-a", kind="reply", text=text)


async def everything(channel: Channel) -> list[Inbound]:
    return [message async for message in channel.receive()]


def test_each_line_arrives_in_order_from_a_known_sender(kind: Kind) -> None:
    async def scenario() -> None:
        played = kind(["Hello there.", "Note one."])
        got = await everything(played.channel)
        assert [message.text for message in got] == ["Hello there.", "Note one."]
        assert [message.kind for message in got] == ["message", "message"]
        assert all(message.sender.verified for message in got)
        assert len({message.native_id for message in got}) == 2

    asyncio.run(scenario())


def test_what_arrives_is_accepted_by_the_inbox_and_can_be_acked(kind: Kind) -> None:
    async def scenario() -> None:
        played = kind(["Hello there."])
        inbox = InMemoryInbox(10_000)
        for message in await everything(played.channel):
            accepted = await inbox.accept(message)
            assert isinstance(accepted, Accepted) and accepted.is_new
            assert await played.channel.ack(message.native_id) is None

    asyncio.run(scenario())


def test_a_send_shows_the_text_and_gives_a_receipt(kind: Kind) -> None:
    async def scenario() -> None:
        played = kind([])
        await everything(played.channel)
        receipt = await played.channel.send(note("The kettle is on."))
        assert isinstance(receipt, DeliveryReceipt)
        assert "The kettle is on." in played.shown()

    asyncio.run(scenario())


def test_a_second_send_shows_it_a_second_time(kind: Kind) -> None:
    async def scenario() -> None:
        played = kind([])
        await everything(played.channel)
        await played.channel.send(note("The kettle is on."))
        await played.channel.send(note("The kettle is on."))
        assert played.shown().count("The kettle is on.") == 2

    asyncio.run(scenario())


def test_a_message_given_again_is_the_same_message_to_the_inbox() -> None:
    async def scenario() -> None:
        first = inbound("n1", "Note one.")
        messages = [first, inbound("n2")]
        channel = ScriptedChannel(messages, FakeClock(), repeat_first=True)
        inbox = InMemoryInbox(10_000)
        news: list[bool] = []
        async for message in channel.receive():
            accepted = await inbox.accept(message)
            assert isinstance(accepted, Accepted)
            news.append(accepted.is_new)
        assert news == [True, True, False]

    asyncio.run(scenario())


def test_conversations_given_out_of_order_each_keep_their_own_order() -> None:
    async def scenario() -> None:
        messages = [
            inbound("a1", session="sess-a"),
            inbound("b1", session="sess-b"),
            inbound("a2", session="sess-a"),
        ]
        channel = ScriptedChannel(messages, FakeClock(), out_of_order=True)
        got = [message.native_id for message in await everything(channel)]
        assert got == ["b1", "a1", "a2"]

    asyncio.run(scenario())


def test_a_send_that_may_have_gone_out_says_so() -> None:
    async def scenario() -> None:
        channel = ScriptedChannel([], FakeClock(), fail_first_send=True)
        failed = await channel.send(note("The kettle is on."))
        assert isinstance(failed, Failure) and failed.effect == "unknown"
        assert isinstance(await channel.send(note("Again.")), DeliveryReceipt)

    asyncio.run(scenario())
