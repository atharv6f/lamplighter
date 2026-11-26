"""The terminal channel: what a typed line becomes, and how a reply looks."""

import asyncio
import threading

import pytest

from lamplighter.adapters import CountingIds, TerminalChannel
from lamplighter.adapters.terminal import PROMPT, REPEAT_LINE, read_lines
from lamplighter.ports import DeliveryReceipt, Failure, Inbound, Outbound, utc_text
from tests.fake_clock import FakeClock
from tests.terminal_kit import Keyboard, Screen, typed


def lines_of(*chunks: bytes) -> list[str | None]:
    """Give what `read_lines` hands over when the reads give these chunks."""
    reads = list(chunks)
    got: list[str | None] = []

    def read() -> bytes:
        return reads.pop(0) if reads else b""

    def hand_over(line: str | None) -> bool:
        got.append(line)
        return True

    read_lines(read, hand_over)
    return got


def test_input_is_split_at_each_line_break() -> None:
    assert lines_of(b"one\ntwo\n") == ["one", "two", None]


def test_a_line_may_come_in_several_reads() -> None:
    assert lines_of(b"o", b"ne\ntw", b"o\n") == ["one", "two", None]


def test_a_character_split_across_two_reads_is_read_whole() -> None:
    kettle = "café\n".encode()
    assert lines_of(kettle[:4], kettle[4:]) == ["café", None]


def test_bytes_that_are_not_utf8_are_replaced_and_the_rest_kept() -> None:
    assert lines_of(b"tea \xff\xfe please\nnext\n") == [
        "tea �� please",
        "next",
        None,
    ]


def test_a_last_line_with_no_line_break_is_still_a_line() -> None:
    assert lines_of(b"one\ntwo") == ["one", "two", None]


def test_an_error_from_the_read_is_the_end_of_input() -> None:
    def read() -> bytes:
        raise OSError(5, "Input/output error")

    got: list[str | None] = []
    read_lines(read, lambda line: got.append(line) is None)
    assert got == [None]


def test_reading_stops_once_no_one_listens() -> None:
    reads = [b"one\ntwo\n", b"three\n"]
    got: list[str | None] = []

    def hand_over(line: str | None) -> bool:
        got.append(line)
        return False

    read_lines(lambda: reads.pop(0) if reads else b"", hand_over)
    assert got == ["one"]
    assert reads == [b"three\n"]


def received(data: bytes, session: str = "sess-a") -> list[Inbound]:
    async def scenario() -> list[Inbound]:
        keyboard = typed(data)
        try:
            channel = terminal(keyboard.reader, Screen(), session)
            return [message async for message in channel.receive()]
        finally:
            keyboard.done()

    return asyncio.run(scenario())


def terminal(reader: int, screen: Screen, session: str = "sess-a") -> TerminalChannel:
    return TerminalChannel(session, FakeClock(), CountingIds(), reader, screen)


def test_a_typed_line_is_a_message_from_you_in_the_session() -> None:
    (message,) = received(b"Note one.\n", session="sess-q")
    assert message.channel == "terminal"
    assert message.conversation == "sess-q"
    assert message.sender.id == "local" and message.sender.verified
    assert (message.kind, message.text, message.command) == (
        "message",
        "Note one.",
        None,
    )
    assert message.sent_at == message.received_at == utc_text(FakeClock().now())


def test_each_message_gets_a_new_id() -> None:
    got = received(b"one\ntwo\nthree\n")
    assert len({message.native_id for message in got}) == 3


@pytest.mark.parametrize("word", ["stop", "/stop", "STOP", "  Stop \t", " /sToP"])
def test_a_stop_word_in_any_spelling_is_a_stop(word: str) -> None:
    (message,) = received(word.encode() + b"\n")
    assert (message.kind, message.command) == ("command", "stop")


@pytest.mark.parametrize("line", ["stop now", "stopped", "/stop!", "please stop"])
def test_a_line_that_only_holds_the_stop_word_is_a_message(line: str) -> None:
    (message,) = received(line.encode() + b"\n")
    assert (message.kind, message.text) == ("message", line)


def test_a_line_with_no_visible_text_is_ignored() -> None:
    got = received(b"\n   \n\t\nNote one.\n \n")
    assert [message.text for message in got] == ["Note one."]


def test_the_stop_the_app_asks_for_is_a_stop_for_the_session() -> None:
    channel = terminal(0, Screen(), "sess-q")
    first, second = channel.stop_message(), channel.stop_message()
    assert (first.kind, first.command, first.conversation) == (
        "command",
        "stop",
        "sess-q",
    )
    assert first.sender.verified
    assert first.native_id != second.native_id


def shown(*sent: Outbound) -> str:
    async def scenario() -> str:
        screen = Screen()
        channel = terminal(0, screen)
        for out in sent:
            assert isinstance(await channel.send(out), DeliveryReceipt)
        return screen.getvalue()

    return asyncio.run(scenario())


def out(text: str, kind: str = "reply", maybe_duplicate: bool = False) -> Outbound:
    return Outbound(
        out_id="o" * 64,
        session_id="sess-a",
        kind="notice" if kind == "notice" else "reply",
        text=text,
        maybe_duplicate=maybe_duplicate,
    )


def test_a_reply_is_printed_as_it_is() -> None:
    assert shown(out("The kettle is on.")) == "The kettle is on.\n"


def test_a_notice_is_printed_in_square_brackets() -> None:
    assert shown(out("Stopped.", "notice")) == "[Stopped.]\n"


def test_a_message_that_may_be_a_repeat_says_so_above_it() -> None:
    printed = shown(out("The kettle is on.", maybe_duplicate=True))
    assert printed == f"{REPEAT_LINE}\nThe kettle is on.\n"
    assert REPEAT_LINE == "(This may be a repeat.)"


def test_the_prompt_and_the_apps_own_lines() -> None:
    screen = Screen()
    channel = terminal(0, screen)
    channel.show_line("[model call 1]")
    channel.show_prompt()
    assert screen.getvalue() == "[model call 1]\n> "
    assert PROMPT == "> "


class ClosedScreen(Screen):
    def write(self, s: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def test_a_send_that_cannot_be_printed_is_a_failure_that_may_have_shown() -> None:
    async def scenario() -> Failure | DeliveryReceipt:
        channel = terminal(0, ClosedScreen())
        return await channel.send(out("The kettle is on."))

    failed = asyncio.run(scenario())
    assert isinstance(failed, Failure)
    assert (failed.kind, failed.effect) == ("unavailable", "unknown")


def test_a_line_typed_after_the_loop_has_closed_ends_the_reader_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    before = set(threading.enumerate())
    keyboard = Keyboard()

    async def scenario() -> None:
        keyboard.type(b"one\n")
        channel = terminal(keyboard.reader, Screen())
        async for _ in channel.receive():
            break

    asyncio.run(scenario())
    (reader,) = set(threading.enumerate()) - before
    assert reader.daemon
    keyboard.type(b"two\n")
    reader.join(5)
    assert not reader.is_alive()
    assert errors == []
    keyboard.done()
