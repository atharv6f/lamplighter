"""The terminal channel: lines you type come in, replies are printed."""

import asyncio
import os
import threading
from collections.abc import AsyncGenerator, Callable
from functools import partial
from typing import Literal, TextIO

from lamplighter.ports import (
    Clock,
    DeliveryReceipt,
    Failure,
    IdSource,
    Inbound,
    Outbound,
    Sender,
    utc_text,
)

CHANNEL = "terminal"
PROMPT = "> "
REPEAT_LINE = "(This may be a repeat.)"
_STOP = "stop"
_STOP_WORDS = (_STOP, "/" + _STOP)
_SOURCE = "Channel/terminal"
_READ_BYTES = 65_536


class TerminalChannel:
    """One session's conversation in a terminal. Satisfies Channel.

    Each line is one message. A line that is `stop` or `/stop`, in any case
    and with spaces around it, is a stop. A blank line is ignored. Input is
    read on a daemon thread, so a line can arrive while a run is going on,
    and the thread never keeps the process alive. `ack` does nothing,
    because a terminal cannot give a line again.
    """

    def __init__(
        self,
        session_id: str,
        clock: Clock,
        ids: IdSource,
        reader: int,
        writer: TextIO,
    ) -> None:
        """Read lines from the file descriptor `reader`, and print to `writer`."""
        self._session_id = session_id
        self._clock = clock
        self._ids = ids
        self._reader = reader
        self._writer = writer

    async def receive(self) -> AsyncGenerator[Inbound]:
        """Yield each line that has visible text, until the input ends."""
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str | None] = asyncio.Queue()
        reading = threading.Thread(
            target=read_lines,
            args=(
                partial(os.read, self._reader, _READ_BYTES),
                partial(_hand_over, loop, lines.put_nowait),
            ),
            daemon=True,
        )
        reading.start()
        while (line := await lines.get()) is not None:
            if not line.strip():
                continue
            if line.strip().lower() in _STOP_WORDS:
                yield self.stop_message()
            else:
                yield self._inbound("message", line, None)

    def stop_message(self) -> Inbound:
        """Give a new stop for the session, as typing `stop` would."""
        return self._inbound("command", _STOP, _STOP)

    def _inbound(
        self, kind: Literal["message", "command"], text: str, command: str | None
    ) -> Inbound:
        now = utc_text(self._clock.now())
        return Inbound(
            channel=CHANNEL,
            native_id=self._ids.new_id(),
            conversation=self._session_id,
            sender=Sender(id="local", verified=True),
            kind=kind,
            text=text,
            sent_at=now,
            received_at=now,
            command=command,
        )

    async def ack(self, native_id: str) -> None:
        """Do nothing: a typed line is never given again."""
        del native_id

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        """Print the message. A notice is put in square brackets."""
        text = f"[{out.text}]" if out.kind == "notice" else out.text
        if out.maybe_duplicate:
            text = f"{REPEAT_LINE}\n{text}"
        try:
            self.show_line(text)
        except OSError as error:
            # The text may be partly on the screen, so it may have been seen.
            return Failure(
                kind="unavailable",
                effect="unknown",
                message=f"The terminal could not be written to: {error}",
                source=_SOURCE,
            )
        return DeliveryReceipt(at=utc_text(self._clock.now()))

    def show_line(self, text: str) -> None:
        """Print one line of the app's own, such as a progress line."""
        self._writer.write(text + "\n")
        self._writer.flush()

    def show_prompt(self) -> None:
        """Print the prompt, to say the agent waits for you."""
        self._writer.write(PROMPT)
        self._writer.flush()


def read_lines(
    read: Callable[[], bytes], hand_over: Callable[[str | None], bool]
) -> None:
    """Read until the input ends, handing over each line and then None.

    The input is split at the line break byte and each line is decoded on
    its own, with bytes that are not UTF-8 replaced. So a read that ends in
    the middle of a character cannot break a line. Text with no line break
    before the end is still one last line. An error from the read is the
    end of input. Reading stops early once `hand_over` says no one listens.
    """
    rest = b""
    while chunk := _read_or_end(read):
        *whole, rest = (rest + chunk).split(b"\n")
        for line in whole:
            if not hand_over(line.decode("utf-8", errors="replace")):
                return
    if rest and not hand_over(rest.decode("utf-8", errors="replace")):
        return
    hand_over(None)


def _read_or_end(read: Callable[[], bytes]) -> bytes:
    try:
        return read()
    except OSError:
        # A terminal that has gone away answers a read with an error, such
        # as EIO once its window is closed. Nothing more can come from it.
        return b""


def _hand_over(
    loop: asyncio.AbstractEventLoop, put: Callable[[str | None], None], line: str | None
) -> bool:
    try:
        loop.call_soon_threadsafe(put, line)
    except RuntimeError:
        # The loop is closed: the process is leaving, and no one reads now.
        return False
    return True
