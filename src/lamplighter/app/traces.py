"""The traces commands: send the spans not yet sent, and start the cursor again."""

from typing import TextIO

from lamplighter.core import TraceProjector
from lamplighter.ports import Clock, EventLog, Failure, TraceCursor, TraceSink

AT = "At position {pos}."
SENT = "Sent {n} spans. The cursor is at position {pos}."
# The failure may be the log's, the sink's or the cursor's, so no part is named.
NOT_SENT = (
    "{message} Run the command again once this is cured. It goes on where this"
    " one stopped."
)
RESET = "The cursor is at 0. The next export sends every span again."
# Long enough that a fast export prints one line, short enough to show life.
_EVERY_S = 5.0


async def export_spans(
    log: EventLog,
    sink: TraceSink,
    cursor: TraceCursor,
    clock: Clock,
    out: TextIO,
    errors: TextIO,
) -> int:
    """Pass until a pass has nothing to do, then say how far it got. Give the exit code.

    The first failure ends the command. Nothing is lost by that: the cursor
    stays where the last stored batch left it.
    """
    projector = TraceProjector(log, sink, cursor)
    sent = 0
    told = clock.monotonic()
    while True:
        done = await projector.run_pass()
        if isinstance(done, Failure):
            errors.write(NOT_SENT.format(message=done.message) + "\n")
            return 1
        sent += done.spans
        if done.events == 0:
            out.write(SENT.format(n=sent, pos=done.pos) + "\n")
            return 0
        if clock.monotonic() - told >= _EVERY_S:
            out.write(AT.format(pos=done.pos) + "\n")
            told = clock.monotonic()


async def reset_cursor(cursor: TraceCursor, out: TextIO, errors: TextIO) -> int:
    """Set the cursor back to 0, so the next export sends every span again."""
    failed = await cursor.reset()
    if failed is not None:
        errors.write(failed.message + "\n")
        return 1
    out.write(RESET + "\n")
    return 0
