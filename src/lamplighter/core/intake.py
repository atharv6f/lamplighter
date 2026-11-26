"""Saves each arriving message in the inbox before the channel is told it arrived."""

import logging
from collections.abc import Callable
from contextlib import aclosing

from lamplighter.core.next_move import is_stop
from lamplighter.core.running_sessions import RunningSessions
from lamplighter.ports import Channel, Failure, Inbound, Inbox

_log = logging.getLogger(__name__)


async def take(
    inbound: Inbound,
    inbox: Inbox,
    channel: Channel,
    running: RunningSessions,
    wake: Callable[[], None],
) -> Failure | None:
    """Save one message, then tell the channel, then wake the driver.

    Only a message and a stop are let in, and only from a verified sender.
    Anything else is acked and dropped, so it can never start a run. A new
    stop also raises its session's stop flag, before the ack, so a slow ack
    cannot delay it. A message the inbox can never save is acked and
    dropped. Any other `Failure` from the inbox is returned.
    """
    if inbound.kind != "message" and not is_stop(inbound):
        _log.warning("A %s that this version does not take was dropped.", inbound.kind)
        await channel.ack(inbound.native_id)
        return None
    if not inbound.sender.verified:
        _log.warning("A message from a sender who is not verified was dropped.")
        await channel.ack(inbound.native_id)
        return None
    accepted = await inbox.accept(inbound)
    if isinstance(accepted, Failure):
        if accepted.kind not in ("invalid_input", "too_large"):
            return accepted
        _log.warning(
            "A message the inbox cannot save was dropped: %s", accepted.message
        )
        await channel.ack(inbound.native_id)
        return None
    if accepted.is_new and is_stop(inbound):
        running.ask_stop(inbound.conversation)
    await channel.ack(inbound.native_id)
    wake()
    return None


async def run_intake(
    channel: Channel,
    inbox: Inbox,
    running: RunningSessions,
    wake: Callable[[], None],
    input_ended: Callable[[], None],
) -> Failure | None:
    """Take every message the channel gives, until its input ends or the inbox fails.

    `input_ended` is called either way, so the driver finishes what is
    waiting and leaves.
    """
    try:
        async with aclosing(channel.receive()) as arriving:
            async for inbound in arriving:
                failed = await take(inbound, inbox, channel, running, wake)
                if failed is not None:
                    return failed
        return None
    finally:
        input_ended()
