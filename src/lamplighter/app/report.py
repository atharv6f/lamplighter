"""The report command: what one day in UTC cost, used and got wrong, from the log."""

from lamplighter.core import DayReport
from lamplighter.ports import EventLog, Failure

# Events read at a time. None is kept once it is counted.
_BATCH = 1_000


async def day_lines(log: EventLog, day: str) -> list[str] | Failure:
    """Read the whole log and give the report of `day`, written as YYYY-MM-DD."""
    report = DayReport(day)
    after = 0
    while True:
        events = await log.tail(after, _BATCH)
        if isinstance(events, Failure):
            return events
        if not events:
            return report.lines()
        for event in events:
            report.count(event)
        after = events[-1].pos
