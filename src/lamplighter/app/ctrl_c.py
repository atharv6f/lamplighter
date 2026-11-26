"""What Ctrl-C does: stop the run, leave at once on a second press, or end."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from lamplighter.core import RunningSessions


class CtrlC:
    """The handler the app registers for the interrupt signal.

    The event loop runs it as an ordinary callback, so it never lands in
    the middle of a write. While the loop runs the session, the first press
    starts a stop, and a second press for the same call of the loop leaves
    at once. With nothing running, a press ends the input, so the process
    leaves once what is waiting is done.
    """

    def __init__(
        self,
        session_id: str,
        running: RunningSessions,
        stop: Callable[[], Coroutine[Any, Any, None]],
        end_input: Callable[[], None],
        leave_now: Callable[[], None],
    ) -> None:
        """Take what each of the three answers to a press needs."""
        self._session_id = session_id
        self._running = running
        self._stop = stop
        self._end_input = end_input
        self._leave_now = leave_now
        self._pressed_during: int | None = None
        self._stopping: set[asyncio.Task[None]] = set()

    def pressed(self) -> None:
        """Answer one press of Ctrl-C."""
        if not self._running.is_running(self._session_id):
            self._end_input()
            return
        call = self._running.begun(self._session_id)
        if self._pressed_during == call:
            self._leave_now()
            return
        self._pressed_during = call
        # The stop goes straight to the inbox, not through the terminal's
        # input, which no one reads while a cut-off run is carried on at
        # start or after the input has ended.
        stopping = asyncio.ensure_future(self._stop())
        self._stopping.add(stopping)
        stopping.add_done_callback(self._stopping.discard)
