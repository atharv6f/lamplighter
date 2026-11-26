"""A channel that plays a list of messages and records what it was asked to show."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from functools import partial

from lamplighter.ports import (
    Clock,
    DeliveryReceipt,
    Failure,
    Inbound,
    Outbound,
    utc_text,
)

_SOURCE = "Channel/scripted"


class ScriptedChannel:
    """Plays its messages, then ends. Satisfies Channel.

    With `one_at_a_time`, each message after the first waits until one more
    message has been shown, as a person who reads each reply before typing
    the next would. The three switches play the channel's hard cases:
    `repeat_first` delivers the first message a second time at the end,
    `out_of_order` delivers the conversations in reverse order while each
    keeps its own order, and `fail_first_send` shows the first message and
    then reports that the send may have failed.
    """

    def __init__(
        self,
        messages: Sequence[Inbound],
        clock: Clock,
        one_at_a_time: bool = False,
        repeat_first: bool = False,
        out_of_order: bool = False,
        fail_first_send: bool = False,
    ) -> None:
        """Take the messages to play, in order, and the switches."""
        self._messages = _ordered(messages, out_of_order, repeat_first)
        self._clock = clock
        self._one_at_a_time = one_at_a_time
        self._fail_next_send = fail_first_send
        self._shown = asyncio.Condition()
        self.sent: list[Outbound] = []
        self.acked: list[str] = []

    async def receive(self) -> AsyncGenerator[Inbound]:
        """Yield each message, waiting for a reply between them if asked to."""
        for place, message in enumerate(self._messages):
            if self._one_at_a_time and place:
                async with self._shown:
                    await self._shown.wait_for(partial(self._has_shown, place))
            yield message

    def _has_shown(self, count: int) -> bool:
        return len(self.sent) >= count

    async def ack(self, native_id: str) -> None:
        """Note the id."""
        self.acked.append(native_id)

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        """Record the message as shown, and give a receipt or the set failure."""
        self.sent.append(out)
        async with self._shown:
            self._shown.notify_all()
        if self._fail_next_send:
            self._fail_next_send = False
            return Failure(
                kind="timeout",
                effect="unknown",
                message="The send was not confirmed.",
                source=_SOURCE,
            )
        return DeliveryReceipt(at=utc_text(self._clock.now()))


def _ordered(
    messages: Sequence[Inbound], out_of_order: bool, repeat_first: bool
) -> list[Inbound]:
    ordered = list(messages)
    if out_of_order:
        conversations = list(dict.fromkeys(m.conversation for m in messages))
        ordered = [
            message
            for conversation in reversed(conversations)
            for message in messages
            if message.conversation == conversation
        ]
    if repeat_first and messages:
        ordered.append(messages[0])
    return ordered
