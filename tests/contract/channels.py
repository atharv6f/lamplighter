"""Builds each kind of channel the contract tests run against.

A test gives the lines a person types. Each kind turns them into a channel
that will deliver them, and can say what it has shown since. The terminal
reads them from a pipe and prints to a screen held in memory.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lamplighter.adapters import CountingIds, ScriptedChannel, TerminalChannel
from lamplighter.ports import Channel
from tests.fake_clock import FakeClock
from tests.run_history import inbound
from tests.terminal_kit import Screen, typed


@dataclass(frozen=True)
class Played:
    """A channel that will deliver the typed lines, and what it has shown."""

    channel: Channel
    shown: Callable[[], str]
    finish: Callable[[], None] = lambda: None


def scripted(lines: Sequence[str]) -> Played:
    """Give the scripted channel, with one message for each line."""
    channel = ScriptedChannel(
        [inbound(f"line-{place}", line) for place, line in enumerate(lines)],
        FakeClock(),
    )
    return Played(channel, lambda: "\n".join(out.text for out in channel.sent))


def terminal(lines: Sequence[str]) -> Played:
    """Give the terminal channel, with the lines typed and the input ended."""
    keyboard = typed("".join(line + "\n" for line in lines).encode())
    screen = Screen()
    channel = TerminalChannel(
        "sess-a", FakeClock(), CountingIds(), keyboard.reader, screen
    )
    return Played(channel, screen.getvalue, keyboard.done)


KINDS: dict[str, Callable[[Sequence[str]], Played]] = {
    "scripted": scripted,
    "terminal": terminal,
}
