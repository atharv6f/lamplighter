"""The traces and report commands: what they send, print, refuse and leave alone."""

import asyncio
import dataclasses
import io
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lamplighter.adapters.in_memory import InMemoryTraceCursor, InMemoryTraceSink
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.app.chat import OFF_THIS_MAC
from lamplighter.app.main import main
from lamplighter.app.report import day_lines
from lamplighter.app.traces import (
    AT,
    NOT_SENT,
    RESET,
    SENT,
    export_spans,
    reset_cursor,
)
from lamplighter.ports import Event, Failure, NewEvent, canonical_json
from tests.fake_clock import FakeClock
from tests.phoenix_stand_in import stand_in
from tests.projector_kit import DOWN, ListedLog, TroubledSink
from tests.span_checks import log_spans
from tests.span_kit import MadeUpLog
from tests.test_day_report import made_up_day

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"
DELETE = "Stop every chat, delete the file and start again."
STALE = Failure(kind="unavailable", effect="none", message="Made up.", source="t")


def runs_log(count: int) -> list[Event]:
    """A log of `count` plain runs, each with its own run id."""
    log = MadeUpLog()
    for number in range(count):
        run = log.run(run_id=f"run-{number}")
        run.start()
        run.answered()
        run.end()
    return log.events


EVENTS = runs_log(60)
TRUTH = {made.span.span_id for made in log_spans(EVENTS)}


@dataclasses.dataclass
class Exported:
    code: int
    out: str
    errors: str


def export(
    log: ListedLog,
    sink: TroubledSink | InMemoryTraceSink,
    cursor: InMemoryTraceCursor,
    clock: FakeClock | None = None,
) -> Exported:
    out, errors = io.StringIO(), io.StringIO()
    code = asyncio.run(
        export_spans(log, sink, cursor, clock or FakeClock(), out, errors)
    )
    return Exported(code, out.getvalue(), errors.getvalue())


def test_an_export_sends_every_span_and_ends_with_the_last_line() -> None:
    sink, cursor = InMemoryTraceSink(), InMemoryTraceCursor()
    done = export(ListedLog(EVENTS), sink, cursor)

    assert (done.code, done.errors) == (0, "")
    assert done.out == SENT.format(n=len(TRUTH), pos=EVENTS[-1].pos) + "\n"
    assert {span.span_id for span in sink.held} == TRUTH


def test_an_export_says_where_it_is_once_5_seconds_have_passed() -> None:
    sink, clock = TroubledSink(), FakeClock()

    async def six_seconds_on(number: int) -> None:
        clock.seconds += 6

    sink.after = six_seconds_on
    done = export(ListedLog(EVENTS), sink, InMemoryTraceCursor(), clock)

    assert done.out.splitlines() == [
        AT.format(pos=200),
        AT.format(pos=EVENTS[-1].pos),
        SENT.format(n=len(TRUTH), pos=EVENTS[-1].pos),
    ]


def troubled(where: str) -> tuple[ListedLog, TroubledSink, InMemoryTraceCursor]:
    log, sink, cursor = ListedLog(EVENTS), TroubledSink(), InMemoryTraceCursor()
    if where == "sink":
        sink.fail_on = {2}
    elif where == "log":

        async def fail_the_log(number: int) -> None:
            log.fail_next_tail(STALE)

        sink.after = fail_the_log
    else:

        async def fail_the_cursor(number: int) -> None:
            if number == 1:
                cursor.fail_next(STALE)

        sink.after = fail_the_cursor
    return log, sink, cursor


@pytest.mark.parametrize("where", ["sink", "log", "cursor"])
def test_a_failure_ends_the_export_and_the_next_one_sends_the_rest(
    where: str,
) -> None:
    log, sink, cursor = troubled(where)
    failed = export(log, sink, cursor)
    sink.fail_on, sink.after = set(), None
    again = export(log, sink, cursor)

    message = DOWN.message if where == "sink" else STALE.message
    assert (failed.code, failed.out) == (1, "")
    assert failed.errors == NOT_SENT.format(message=message) + "\n"
    assert again.code == 0
    assert {span.span_id for span in sink.store.held} == TRUTH
    mark = asyncio.run(cursor.read())
    assert not isinstance(mark, Failure) and mark.pos == EVENTS[-1].pos


def test_a_second_export_sends_nothing() -> None:
    log, sink, cursor = ListedLog(EVENTS), InMemoryTraceSink(), InMemoryTraceCursor()
    export(log, sink, cursor)
    again = export(log, sink, cursor)

    assert again.out == SENT.format(n=0, pos=EVENTS[-1].pos) + "\n"
    assert set(sink.copies.values()) == {1}


def test_a_reset_then_an_export_sends_every_span_again() -> None:
    log, sink, cursor = ListedLog(EVENTS), InMemoryTraceSink(), InMemoryTraceCursor()
    export(log, sink, cursor)
    out = io.StringIO()
    assert asyncio.run(reset_cursor(cursor, out, io.StringIO())) == 0
    again = export(log, sink, cursor)

    assert out.getvalue() == RESET + "\n"
    assert again.out == SENT.format(n=len(TRUTH), pos=EVENTS[-1].pos) + "\n"
    assert sink.copies == dict.fromkeys(TRUTH, 2)


def test_a_reset_that_fails_prints_the_failure_and_gives_1() -> None:
    cursor, errors = InMemoryTraceCursor(), io.StringIO()
    cursor.fail_next(STALE)

    assert asyncio.run(reset_cursor(cursor, io.StringIO(), errors)) == 1
    assert errors.getvalue() == "Made up.\n"


def test_the_fixed_texts_are_the_ones_in_the_spec() -> None:
    assert AT == "At position {pos}."
    assert SENT == "Sent {n} spans. The cursor is at position {pos}."
    assert NOT_SENT == (
        "{message} Run the command again once this is cured. It goes on where"
        " this one stopped."
    )
    assert RESET == "The cursor is at 0. The next export sends every span again."


# The commands as the entry point runs them, on a real store.


def settings(tmp_path: Path, phoenix_url: str = "http://127.0.0.1:6006") -> str:
    path = tmp_path / "lamplighter.toml"
    text = SETTINGS.read_text()
    text = text.replace('dir = "~/.lamplighter"', f'dir = "{tmp_path / "store"}"')
    text = text.replace('"http://127.0.0.1:6006"', f'"{phoenix_url}"')
    path.write_text(text)
    return str(path)


def saved(tmp_path: Path, events: list[Event]) -> None:
    """Save the made-up events in a real store, in the same order."""
    folder = tmp_path / "store"
    folder.mkdir(mode=0o700, exist_ok=True)
    connection = open_database(folder / "state.db", 5.0)
    assert not isinstance(connection, Failure)
    log = SqliteEventLog(connection, {}, 100_000)

    async def save() -> None:
        for event in events:
            new = NewEvent(
                type=event.type,
                v=event.v,
                payload=event.payload,
                at=event.at,
                run_id=event.run_id,
                step=event.step,
                duration_ms=event.duration_ms,
                cause=event.cause,
                event_id=event.event_id if event.type == "user_message" else None,
            )
            assert await log.append(event.session_id, [new], event.seq - 1) == event.seq

    try:
        asyncio.run(save())
    finally:
        connection.close()


@pytest.mark.parametrize("action", ["export", "reset"])
def test_both_traces_commands_refuse_a_trace_store_off_this_mac(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    marker = "made-up-marker-in-the-setting"
    path = settings(tmp_path, f"http://localhost:6006/{marker}")
    saved(tmp_path, EVENTS[:3])

    assert main(["--config", path, "traces", action]) == 1
    err = capsys.readouterr().err
    assert err == OFF_THIS_MAC + "\n"
    assert marker not in err
    assert not (tmp_path / "store" / "trace_cursor.json").exists()


def test_a_reset_with_no_store_folder_says_so_and_makes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", settings(tmp_path), "traces", "reset"]) == 0
    assert capsys.readouterr().out == RESET + "\n"
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize("action", ["export", "reset"])
def test_a_cursor_file_this_program_did_not_write_is_left_as_it_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    saved(tmp_path, EVENTS[:3])
    cursor_file = tmp_path / "store" / "trace_cursor.json"
    cursor_file.write_bytes(b'{"made": "up"}')

    assert main(["--config", settings(tmp_path), "traces", action]) == 1
    assert DELETE in capsys.readouterr().err
    assert cursor_file.read_bytes() == b'{"made": "up"}'


def test_an_export_from_the_entry_point_reaches_the_trace_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved(tmp_path, EVENTS)
    with stand_in() as server:
        code = main(["--config", settings(tmp_path, server.url), "traces", "export"])
        held = set(server.names("lamplighter"))

    assert code == 0
    assert capsys.readouterr().out == (
        SENT.format(n=len(TRUTH), pos=EVENTS[-1].pos) + "\n"
    )
    assert held == TRUTH
    written = (tmp_path / "store" / "trace_cursor.json").read_text()
    target = canonical_json([server.url, "lamplighter", 1])
    assert written == canonical_json(
        {"cursors": {target: {"pos": EVENTS[-1].pos, "resets": 0}}, "v": 1}
    )


def test_an_export_with_no_store_gives_the_store_commands_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", settings(tmp_path), "traces", "export"]) == 1
    state = tmp_path / "store" / "state.db"
    assert capsys.readouterr().err == f"There is no store at {state}.\n"


def test_the_report_prints_the_day_from_the_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved(tmp_path, made_up_day().events)
    path = settings(tmp_path)

    assert main(["--config", path, "report", "--day", "2026-11-26"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == [
        "Day 2026-11-26, 00:00 to 24:00 UTC",
        "Runs ended: 3 (1 done, 1 failed, 1 limit)",
        "Steps: 5",
    ]
    assert len(lines) == 22


@pytest.mark.parametrize(
    "day", ["2026-13-01", "2026-02-30", "26-11-2026", "20261126", "2026-11-26 ", ""]
)
def test_a_day_that_is_not_a_date_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], day: str
) -> None:
    with pytest.raises(SystemExit) as ended:
        main(["--config", settings(tmp_path), "report", "--day", day])
    assert ended.value.code == 2
    assert "not a day as YYYY-MM-DD" in capsys.readouterr().err


def test_a_report_with_no_store_gives_the_store_commands_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", settings(tmp_path), "report"]) == 1
    state = tmp_path / "store" / "state.db"
    assert capsys.readouterr().err == f"There is no store at {state}.\n"


def test_the_report_reads_the_whole_log_however_long() -> None:
    events = runs_log(300)
    log = ListedLog(events)
    lines = asyncio.run(day_lines(log, "2026-11-26"))

    assert len(events) > 1_000
    assert not isinstance(lines, Failure)
    assert "Steps: 300" in lines
    assert "Runs ended: 300 (300 done)" in lines


@pytest.fixture
def local_date_not_utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Put the machine in a zone whose date is not the date in UTC right now."""
    # At any hour of the UTC day one of these two zones is on another date.
    far = "Etc/GMT-14" if datetime.now(UTC).hour >= 10 else "Etc/GMT+12"
    monkeypatch.setenv("TZ", far)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("local_date_not_utc")
def test_a_report_with_no_day_is_of_the_day_now_in_utc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert datetime.now().date() != datetime.now(UTC).date()
    saved(tmp_path, EVENTS[:3])
    before = datetime.now(UTC).date().isoformat()
    assert main(["--config", settings(tmp_path), "report"]) == 0
    after = datetime.now(UTC).date().isoformat()

    first = capsys.readouterr().out.splitlines()[0]
    assert first in {f"Day {day}, 00:00 to 24:00 UTC" for day in (before, after)}
