"""Counts the spans a log promises, without the function that makes them.

The count comes from the rules of the four spans alone, so a test that
compares it with what the span function gave finds a span that is missing
or one that was made up. Run as a command, it compares the store in the
settings with the spans Phoenix lists:

    uv run python -m tests.span_count [--config lamplighter.toml]
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from lamplighter.adapters.phoenix import local_address
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.app.config import load_config
from lamplighter.ports import Event, Failure
from tests.phoenix_process import listed_spans

_BATCH = 1_000


def promised_spans(events: Sequence[Event]) -> int:
    """Give the number of spans that are whole in `events`, a log in order.

    A run is told apart by its session id and its run id together, because
    two sessions may use one run id. Each ended run counts one, each tool
    result of a step that has its request one, each request that is not its
    run's last event one, and each request that a later request or the run's
    end follows one more.
    """
    runs: defaultdict[tuple[str, str], list[Event]] = defaultdict(list[Event])
    for event in events:
        if event.run_id is not None:
            runs[(event.session_id, event.run_id)].append(event)
    return sum(_run_count(run) for run in runs.values())


def _run_count(run: Sequence[Event]) -> int:
    if not any(event.type == "run_started" for event in run):
        return 0
    asked = {event.step for event in run if event.type == "model_request"}
    count = sum(1 for event in run if event.type == "run_end")
    count += sum(
        1 for event in run if event.type == "tool_result" and event.step in asked
    )
    for index, event in enumerate(run):
        if event.type != "model_request":
            continue
        later = [after.type for after in run[index + 1 :]]
        count += bool(later)
        count += "model_request" in later or "run_end" in later
    return count


def main(argv: Sequence[str] | None = None) -> int:
    """Print the spans the store's log promises and the spans Phoenix lists."""
    parser = argparse.ArgumentParser(prog="python -m tests.span_count")
    parser.add_argument("--config", type=Path, default=Path("lamplighter.toml"))
    config = load_config(parser.parse_args(argv).config)
    address = local_address(config.traces.phoenix_url)
    if address is None:
        print("traces.phoenix_url is not an address on this Mac.", file=sys.stderr)
        return 1
    events = _whole_log(Path(config.store.dir) / "state.db")
    if isinstance(events, Failure):
        print(events.message, file=sys.stderr)
        return 1
    project = config.traces.project
    listed = len(listed_spans(address, project))
    print(
        f"The log promises {promised_spans(events)} spans."
        f" Phoenix lists {listed} in project {project}."
    )
    return 0


def _whole_log(state: Path) -> list[Event] | Failure:
    # Opening a file that is not there would make an empty store.
    if not state.is_file():
        return Failure("unavailable", "none", f"There is no store at {state}.", "t")
    connection = open_database(state, 5.0)
    if isinstance(connection, Failure):
        return connection
    try:
        return asyncio.run(_read_all(SqliteEventLog(connection, {}, 1 << 30)))
    finally:
        connection.close()


async def _read_all(log: SqliteEventLog) -> list[Event] | Failure:
    events: list[Event] = []
    while True:
        batch = await log.tail(events[-1].pos if events else 0, _BATCH)
        if isinstance(batch, Failure):
            return batch
        if not batch:
            return events
        events += batch


if __name__ == "__main__":
    sys.exit(main())
