"""A trace cursor kept in memory, for tests: a position and a count of resets."""

from lamplighter.ports import CursorMark, Failure


class InMemoryTraceCursor:
    """Holds one mark. Satisfies TraceCursor.

    A test can make the next call of any kind return a failure of its choosing.
    """

    def __init__(self) -> None:
        self._mark = CursorMark(pos=0, resets=0)
        self._next_failure: Failure | None = None

    def fail_next(self, failure: Failure) -> None:
        """Make the next read, move or reset return `failure` and change nothing."""
        self._next_failure = failure

    async def read(self) -> CursorMark | Failure:
        """Give the mark."""
        return self._failed() or self._mark

    async def move(self, seen: CursorMark, to: int) -> bool | Failure:
        """Move to `to` if the mark is still `seen`, and say whether it moved."""
        failed = self._failed()
        if failed is not None:
            return failed
        if self._mark != seen:
            return False
        self._mark = CursorMark(pos=to, resets=seen.resets)
        return True

    async def reset(self) -> Failure | None:
        """Set the position to 0 and count one more reset."""
        failed = self._failed()
        if failed is None:
            self._mark = CursorMark(pos=0, resets=self._mark.resets + 1)
        return failed

    def _failed(self) -> Failure | None:
        failed, self._next_failure = self._next_failure, None
        return failed
