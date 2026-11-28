"""Where the capture set's runs come from: chat sessions in the store, and bench trials.

Each source gives the runs it could rebuild, and counts the runs it
passed over because no settings file at hand matches the config they
ran under.
"""

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lamplighter.app.capture_set import CapturedRun, Rebuild, run_requests
from lamplighter.bench import read_record
from lamplighter.core import UPCASTERS
from lamplighter.ports import (
    RUN_STARTED,
    BenchJobs,
    Event,
    EventLog,
    Failure,
    LedgerRow,
    RunSetRow,
    from_plain,
    read_saved_json,
    upcast,
)

EXPORTED_EVENTS = Path("harness") / "export" / "events.jsonl"


@dataclass(frozen=True, slots=True)
class Gathered:
    """The runs one source could rebuild, and how many runs it passed over."""

    runs: tuple[CapturedRun, ...]
    passed_over: int


@dataclass(frozen=True, slots=True)
class TrialSettings:
    """A run set's settings copy: its portable config id and what a request takes."""

    portable_id: str
    rebuild: Rebuild


async def chat_runs(
    log: EventLog, config_ids: frozenset[str], rebuild: Rebuild
) -> Gathered | Failure:
    """Rebuild every run in the store that started under one of these config ids.

    The ids are those of the settings file, private and not, so a private
    session's runs are found too. Every other run is passed over.
    """
    sessions = await log.sessions(None)
    if isinstance(sessions, Failure):
        return sessions
    runs: list[CapturedRun] = []
    passed = 0
    for info in sessions:
        events = await log.read(info.session_id, 0)
        if isinstance(events, Failure):
            return events
        for start in (e for e in events if e.type == RUN_STARTED):
            if start.run_id is None or start.payload.get("config_id") not in config_ids:
                passed += 1
                continue
            run = run_requests(events, start.run_id, rebuild, "chat")
            if isinstance(run, Failure):
                return run
            runs.append(run)
    return Gathered(tuple(runs), passed)


async def trial_runs(
    rows: Sequence[LedgerRow],
    jobs: BenchJobs,
    run_sets: Path,
    settings: Callable[[Path], TrialSettings | str],
) -> Gathered | Failure:
    """Rebuild each trial of every run set on the dev split, with its settings copy.

    A held-out run set is never opened. A trial is passed over when its
    record is missing, names no run, or names another portable config id
    than its run set's settings copy, and so is every trial of a run set
    whose copy cannot be read.
    """
    runs: list[CapturedRun] = []
    passed = 0
    for name in _dev_run_sets(rows):
        found = await jobs.trials(name)
        if isinstance(found, Failure):
            return found
        copy = settings(run_sets / name / "settings.toml")
        for facts in found:
            record = None if facts.record is None else read_record(facts.record)
            if (
                isinstance(copy, str)
                or record is None
                or record.run_id is None
                or record.portable_config_id != copy.portable_id
            ):
                passed += 1
                continue
            events = exported_events(Path(facts.folder) / EXPORTED_EVENTS)
            if isinstance(events, Failure):
                return events
            run = run_requests(events, record.run_id, copy.rebuild, "trial")
            if isinstance(run, Failure):
                return run
            runs.append(run)
    return Gathered(tuple(runs), passed)


def exported_events(path: Path) -> tuple[Event, ...] | Failure:
    """Read a session's exported events, each in the newest form this code knows."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return _failed(f"The export {path} could not be read: {error.strerror}.")
    events: list[Event] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            saved = from_plain(Event, read_saved_json(line))
        except ValueError as error:
            return _failed(f"Line {number} of {path} is not an event: {error}.")
        v, payload = upcast(saved.type, saved.v, saved.payload, UPCASTERS)
        events.append(dataclasses.replace(saved, v=v, payload=payload))
    return tuple(events)


def _dev_run_sets(rows: Sequence[LedgerRow]) -> list[str]:
    names: list[str] = []
    for row in rows:
        if isinstance(row, RunSetRow) and row.body.run_set.get("split") == "dev":
            name = row.body.run_set.get("run_set")
            if isinstance(name, str):
                names.append(name)
    return names


def _failed(message: str) -> Failure:
    return Failure(
        kind="invalid_input", effect="none", message=message, source="capture_set"
    )
