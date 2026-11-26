"""Builds each kind of channel the contract tests run against.

A test gives the lines a person types. Each kind turns them into a channel
that will deliver them, and can say what it has shown since.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lamplighter.adapters import ScriptedChannel
from lamplighter.ports import Channel
from tests.fake_clock import FakeClock
from tests.run_history import inbound


@dataclass(frozen=True)
class Played:
    """A channel that will deliver the typed lines, and what it has shown."""

    channel: Channel
    shown: Callable[[], str]


def scripted(lines: Sequence[str]) -> Played:
    """Give the scripted channel, with one message for each line."""
    channel = ScriptedChannel(
        [inbound(f"line-{place}", line) for place, line in enumerate(lines)],
        FakeClock(),
    )
    return Played(channel, lambda: "\n".join(out.text for out in channel.sent))


KINDS: dict[str, Callable[[Sequence[str]], Played]] = {"scripted": scripted}
