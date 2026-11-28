"""Harbor's result files of one job, read into the facts of each finished trial.

The names Harbor gives its fields appear in this file and nowhere else.
Each was read in the result files of Harbor 0.27.0.
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from lamplighter.ports import CheckEnd, Failure, RecordedTokens, TrialFacts

_SOURCE = "harbor_jobs"
_RESULT = "result.json"
RECORD = Path("harness") / "trial_record.json"
_TASK_FRONT = "lamplighter-bench/"
# Harbor writes this for a trial it ended itself on an interrupt, so that
# a resume runs it again.
CANCELLED = "CancelledError"
_TIMED_OUT = "VerifierTimeoutError"
# Harbor counts these as graded with no valid reward. The bench counts each
# as a check that failed, so they are read apart from every other reason.
_UNREADABLE = frozenset(
    {"RewardFileNotFoundError", "RewardFileEmptyError", "VerifierOutputParseError"}
)


_ONE_MS = timedelta(milliseconds=1)


class _NotHarbors(Exception):
    """A result file that is not of the shape Harbor writes."""


class _Fields:
    """One result file, read field by field, each a refusal when it is not there."""

    def __init__(self, trial: str, plain: object) -> None:
        """Keep the trial's name, for the refusal, and the parsed file."""
        self.trial = trial
        self._plain = plain

    def value(self, *keys: str) -> object:
        """Give the value at the keys, or None when a level above it is null."""
        here = self._plain
        for key in keys:
            if here is None:
                return None
            if not isinstance(here, dict) or key not in here:
                raise _NotHarbors(f"{self.trial} has no {'.'.join(keys)}.")
            here = cast(dict[str, object], here)[key]
        return here

    def text(self, *keys: str) -> str:
        """Give a text that must be there."""
        found = self.value(*keys)
        if not isinstance(found, str) or not found:
            raise _NotHarbors(f"{self.trial} has no text at {'.'.join(keys)}.")
        return found

    def text_or_none(self, *keys: str) -> str | None:
        """Give a text that may be null."""
        found = self.value(*keys)
        if found is not None and not isinstance(found, str):
            raise _NotHarbors(f"{self.trial} has a {'.'.join(keys)} that is no text.")
        return found

    def count_or_none(self, *keys: str) -> int | None:
        """Give a whole number from 0 that may be null."""
        found = self.value(*keys)
        if found is None or (type(found) is int and found >= 0):
            return found
        raise _NotHarbors(f"{self.trial} has a {'.'.join(keys)} that is no count.")

    def flag(self, *keys: str) -> bool:
        """Give a true or false that must be there."""
        found = self.value(*keys)
        if not isinstance(found, bool):
            raise _NotHarbors(f"{self.trial} has no true or false at {keys[-1]}.")
        return found

    def time(self, *keys: str) -> datetime:
        """Give a time with its offset that must be there."""
        try:
            found = datetime.fromisoformat(self.text(*keys))
        except ValueError:
            found = None
        if found is None or found.utcoffset() is None:
            raise _NotHarbors(f"{self.trial} has no time at {'.'.join(keys)}.")
        return found


def finished_trials(job_folder: Path) -> list[TrialFacts] | Failure:
    """Give the facts of every finished trial in a job folder, by folder name.

    A trial folder with no result file was cut off, and one that Harbor
    recorded as cancelled will be run again: neither is given. A folder
    that is not there holds no trial yet.
    """
    try:
        if not job_folder.is_dir():
            return []
        folders = sorted(p for p in job_folder.iterdir() if p.is_dir())
        found: list[TrialFacts] = []
        for folder in folders:
            facts = _trial(folder)
            if facts is not None:
                found.append(facts)
        return found
    except _NotHarbors as wrong:
        return Failure(
            "unavailable",
            "none",
            f"A result file is not of the shape Harbor writes: {wrong}",
            _SOURCE,
        )
    except OSError as error:
        return Failure(
            "unavailable",
            "none",
            f"The job folder {job_folder} could not be read: {error.strerror}.",
            _SOURCE,
        )


def _trial(folder: Path) -> TrialFacts | None:
    result = folder / _RESULT
    if not result.is_file():
        return None
    try:
        plain: object = json.loads(result.read_bytes())
    except ValueError:
        raise _NotHarbors(f"{folder.name} is not JSON.") from None
    read = _Fields(folder.name, plain)
    stopped = read.text_or_none("exception_info", "exception_type") or ""
    if stopped == CANCELLED:
        return None
    started = read.time("started_at")
    record = folder / RECORD
    return TrialFacts(
        task=read.text("task_name").removeprefix(_TASK_FRONT),
        runner_name=read.text("trial_name"),
        started_at=started.isoformat(),
        ms=_ms(started, read.time("finished_at"), folder.name),
        check=_check(read, stopped),
        stopped_by=stopped,
        task_folder_hash=read.text("task_checksum"),
        harness_version=read.text("agent_info", "version"),
        tokens=_tokens(read),
        micro_dollars=_micro_dollars(read),
        record=record.read_text(encoding="utf-8") if record.is_file() else None,
        folder=str(folder),
    )


def _check(read: _Fields, stopped: str) -> CheckEnd:
    if read.flag("reward_valid"):
        rewards = read.value("verifier_result", "rewards")
        if not isinstance(rewards, dict) or not rewards:
            raise _NotHarbors(f"{read.trial} has a valid reward and no rewards.")
        values = list(cast(dict[str, object], rewards).values())
        return "passed" if values == [1] and type(values[0]) is not bool else "failed"
    if stopped == _TIMED_OUT:
        return "timed_out"
    if stopped in _UNREADABLE:
        return "unreadable_reward"
    return "none"


def _tokens(read: _Fields) -> RecordedTokens | None:
    counts = [
        read.count_or_none("agent_result", name)
        for name in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
    ]
    match counts:
        case [int() as given, int() as cached, int() as out] if given >= cached:
            return RecordedTokens(input=given, cache_read=cached, output=out)
        case [None, None, None]:
            return None
        case _:
            raise _NotHarbors(f"{read.trial} has token counts that do not fit.")


def _micro_dollars(read: _Fields) -> int | None:
    cost = read.value("agent_result", "cost_usd")
    if cost is None:
        return None
    if not isinstance(cost, int | float) or isinstance(cost, bool):
        raise _NotHarbors(f"{read.trial} has a cost that is not a number.")
    if not math.isfinite(cost) or cost < 0:
        raise _NotHarbors(f"{read.trial} has a cost below 0 or with no end.")
    return round(cost * 1_000_000)


def _ms(started: datetime, finished: datetime, trial: str) -> int:
    span = finished - started
    if span.total_seconds() < 0:
        raise _NotHarbors(f"{trial} finished before it started.")
    return span // _ONE_MS
