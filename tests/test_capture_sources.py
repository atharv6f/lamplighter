"""Where the capture set's runs come from: chat sessions, and dev-split trials."""

import asyncio
import dataclasses
from collections.abc import Sequence
from pathlib import Path

from lamplighter.adapters.in_memory import InMemoryEventLog
from lamplighter.app.capture_set import CapturedRun
from lamplighter.app.capture_sources import (
    EXPORTED_EVENTS,
    TrialSettings,
    chat_runs,
    exported_events,
    trial_runs,
)
from lamplighter.bench import record_text
from lamplighter.core import UPCASTERS
from lamplighter.ports import (
    Event,
    Failure,
    Job,
    LedgerRow,
    NewEvent,
    TextPart,
    TrialFacts,
    saved_json,
    to_plain,
)
from tests.ledger_kit import loose, run_set
from tests.run_history import History
from tests.test_bench_trial import SPENT
from tests.test_capture_set import REBUILD

MATCHED = "a" * 64
PRIVATE = "b" * 64
OTHER = "c" * 64
PORTABLE = "p" * 64


def answered_run(history: History, text: str, run_id: str, config_id: str) -> None:
    history.start(text, f"m-{run_id}", run_id=run_id, config_id=config_id)
    history.chose()
    history.answered(f"Answer to {text}")


async def saved(*histories: History) -> InMemoryEventLog:
    log = InMemoryEventLog(UPCASTERS, 1_000_000)
    for history in histories:
        events = [
            NewEvent(
                type=e.type,
                v=e.v,
                payload=e.payload,
                at=e.at,
                run_id=e.run_id,
                step=e.step,
                duration_ms=e.duration_ms,
                cause=e.cause,
                event_id=e.event_id if e.type == "user_message" else None,
            )
            for e in history.events
        ]
        assert await log.append(history.session, events, 0) == len(events)
    return log


def asked(runs: Sequence[CapturedRun]) -> list[str]:
    """Give the last text each run's first request ends with."""
    found: list[str] = []
    for gathered in runs:
        part = gathered.requests[0].request.messages[-1].parts[0]
        assert isinstance(part, TextPart)
        found.append(part.text)
    return found


def test_chat_runs_under_the_settings_ids_are_rebuilt_and_others_counted() -> None:
    kitchen, garden = History("kitchen"), History("garden")
    answered_run(kitchen, "Kettle?", "run-1", MATCHED)
    answered_run(kitchen, "Milk?", "run-2", OTHER)
    answered_run(garden, "Roses?", "run-3", PRIVATE)
    log = asyncio.run(saved(kitchen, garden))
    found = asyncio.run(chat_runs(log, frozenset({MATCHED, PRIVATE}), REBUILD))
    assert not isinstance(found, Failure), found
    assert found.passed_over == 1
    assert [r.source for r in found.runs] == ["chat", "chat"]
    # By session id: the garden's run first.
    assert asked(found.runs) == ["Roses?", "Kettle?"]


class ListedJobs:
    """Gives each run set the trials a test lists for it, and runs nothing."""

    def __init__(self, trials: dict[str, list[TrialFacts]]) -> None:
        self.trials_by_set = trials
        self.asked: list[str] = []

    async def run(self, job: Job) -> Failure | None:
        raise AssertionError("the capture set runs no job")

    async def trials(self, run_set: str) -> Sequence[TrialFacts] | Failure:
        self.asked.append(run_set)
        return self.trials_by_set.get(run_set, [])


def exported(folder: Path, events: Sequence[Event]) -> None:
    path = folder / EXPORTED_EVENTS
    path.parent.mkdir(parents=True)
    path.write_text("".join(saved_json(to_plain(e)) + "\n" for e in events))


def facts(folder: Path, record: str | None) -> TrialFacts:
    return TrialFacts(
        task="made-up-task",
        runner_name=folder.name,
        started_at="2026-11-28T10:00:00+00:00",
        ms=60_000,
        check="passed",
        stopped_by="",
        task_folder_hash="h" * 64,
        harness_version="made-up-version",
        tokens=None,
        micro_dollars=None,
        record=record,
        folder=str(folder),
    )


def a_trial(folder: Path, text: str, portable: str = PORTABLE) -> TrialFacts:
    history = History("bench-trial")
    answered_run(history, text, "run-1", OTHER)
    exported(folder, history.events)
    record = dataclasses.replace(SPENT, portable_config_id=portable, run_id="run-1")
    return facts(folder, record_text(record))


def copies(path: Path) -> TrialSettings | str:
    if path.parent.name == "unreadable":
        return f"{path}: made up."
    return TrialSettings(PORTABLE, REBUILD)


def test_trials_of_dev_run_sets_are_rebuilt_and_held_out_ones_never_opened(
    tmp_path: Path,
) -> None:
    rows: list[LedgerRow] = [
        run_set("dev-one", "baseline"),
        run_set("held", "baseline", split="held_out"),
        loose("loose-one"),
        loose("unreadable"),
    ]
    jobs = ListedJobs(
        {
            "dev-one--baseline": [
                a_trial(tmp_path / "t1", "Dev one."),
                a_trial(tmp_path / "t2", "Other id.", portable="q" * 64),
                facts(tmp_path / "t3", None),
            ],
            "held--baseline": [a_trial(tmp_path / "t4", "Held out.")],
            "loose-one": [a_trial(tmp_path / "t5", "Loose.")],
            "unreadable": [a_trial(tmp_path / "t6", "Unreadable copy.")],
        }
    )
    found = asyncio.run(trial_runs(rows, jobs, tmp_path / "run_sets", copies))
    assert not isinstance(found, Failure), found
    assert jobs.asked == ["dev-one--baseline", "loose-one", "unreadable"]
    assert found.passed_over == 3
    assert asked(found.runs) == ["Dev one.", "Loose."]
    assert {r.source for r in found.runs} == {"trial"}


def test_a_trial_with_no_run_is_passed_over(tmp_path: Path) -> None:
    record = dataclasses.replace(SPENT, portable_config_id=PORTABLE, run_id=None)
    jobs = ListedJobs({"loose-one": [facts(tmp_path / "t", record_text(record))]})
    found = asyncio.run(trial_runs([loose("loose-one")], jobs, tmp_path, copies))
    assert not isinstance(found, Failure)
    assert (found.runs, found.passed_over) == ((), 1)


def test_an_export_is_read_with_its_old_events_brought_up(tmp_path: Path) -> None:
    history = History("bench-trial")
    answered_run(history, "Old.", "run-1", OTHER)
    old = [
        dataclasses.replace(e, v=1, payload={"config_id": OTHER, "trigger": "message"})
        if e.type == "run_started"
        else e
        for e in history.events
    ]
    exported(tmp_path, old)
    read = exported_events(tmp_path / EXPORTED_EVENTS)
    assert read == tuple(history.events)


def test_an_export_that_is_missing_or_broken_stops_the_build(tmp_path: Path) -> None:
    missing = exported_events(tmp_path / "missing.jsonl")
    assert isinstance(missing, Failure)
    assert "could not be read" in missing.message
    (tmp_path / "broken.jsonl").write_text('{"made": "up"}\n')
    broken = exported_events(tmp_path / "broken.jsonl")
    assert isinstance(broken, Failure)
    assert "Line 1" in broken.message
