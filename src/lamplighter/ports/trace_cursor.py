"""The port for how far the log has been turned into stored spans."""

from dataclasses import dataclass
from typing import Protocol

from lamplighter.ports.failure import Failure


@dataclass(frozen=True, slots=True)
class CursorMark:
    """What one read of the cursor saw: its log position and its count of resets."""

    pos: int
    resets: int


class TraceCursor(Protocol):
    """The log position up to which every finished span is in the trace store.

    Callers can rely on this:

    1. `read` gives the cursor's mark. A cursor that was never moved and
       never reset is at position 0 with 0 resets.
    2. `move(seen, to)` puts the cursor at `to` only if its mark is still
       `seen` at that moment, and says whether it did. Looking and writing
       are one step: no other `move` and no `reset` of the same cursor
       lands between them.
    3. `reset` puts the position at 0 and adds 1 to the count of resets, so
       no mark from before a reset equals a mark from after it, whatever
       the positions are.
    4. A caller only moves the cursor forward: `to` is above `seen.pos`.
       With that and promise 3, a mark that was true once and is true no
       longer never becomes true again.
    5. After a kill at any moment, `read` gives the new mark or the old
       one, never a mix.
    6. A `move` or a `reset` may be lost to a power cut. The cursor is then
       as it was before the call, or at position 0 with 0 resets. A lost
       `move` costs spans sent twice. A lost `reset` did not happen and
       must be run again.
    7. A failure is a value. `unavailable` means the cursor could not be
       read or written, or what was read is not a cursor.
    """

    async def read(self) -> CursorMark | Failure:
        """Give the cursor's mark."""
        ...

    async def move(self, seen: CursorMark, to: int) -> bool | Failure:
        """Move to `to` if the mark is still `seen`, and say whether it moved."""
        ...

    async def reset(self) -> Failure | None:
        """Set the position to 0 and count one more reset."""
        ...
