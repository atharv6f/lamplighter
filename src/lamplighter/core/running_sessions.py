"""Which sessions this process is running, and which have been asked to stop."""

import asyncio

from lamplighter.core.interruptible import StopSignal


class RunningSessions:
    """Shared by the whole process. Touches nothing outside it.

    A stop flag only wakes a wait. The inbox is the truth: a stop is saved
    there first, and the flag is set after. Each pass of the loop clears the
    flag before it reads the inbox, so a flag set after its stop was used
    cannot cut a later call.
    """

    def __init__(self) -> None:
        """Start with no session running and no stop asked for."""
        self._running: set[str] = set()
        self._begun: dict[str, int] = {}
        self._stops: dict[str, asyncio.Event] = {}

    def begin(self, session_id: str) -> bool:
        """Mark the session as running. False if it was already."""
        if session_id in self._running:
            return False
        self._running.add(session_id)
        self._begun[session_id] = self.begun(session_id) + 1
        return True

    def begun(self, session_id: str) -> int:
        """Say how many calls of the loop have begun on the session in this process.

        A caller that remembers the count can tell whether the call running
        now is the one it saw before.
        """
        return self._begun.get(session_id, 0)

    def end(self, session_id: str) -> None:
        """Mark the session as no longer running."""
        self._running.discard(session_id)

    def is_running(self, session_id: str) -> bool:
        """Say whether a call of the loop is running the session now."""
        return session_id in self._running

    def ask_stop(self, session_id: str) -> None:
        """Wake whatever waits on the session's stop. The stop is already saved."""
        self._flag(session_id).set()

    def clear_stop(self, session_id: str) -> None:
        """Take the flag down, before a pass reads the inbox."""
        self._flag(session_id).clear()

    def stop_asked(self, session_id: str) -> bool:
        """Say whether the flag is up."""
        return self._flag(session_id).is_set()

    async def wait_for_stop(self, session_id: str) -> None:
        """Return once the flag is up."""
        await self._flag(session_id).wait()

    def signal_for(self, session_id: str) -> StopSignal:
        """Give the race its view of this session's stop."""
        return StopSignal(
            known=lambda: self.stop_asked(session_id),
            wait=lambda: self.wait_for_stop(session_id),
        )

    def _flag(self, session_id: str) -> asyncio.Event:
        return self._stops.setdefault(session_id, asyncio.Event())
