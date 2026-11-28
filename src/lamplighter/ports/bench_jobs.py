"""What the job runner recorded of each trial of the bench, as plain values."""

from dataclasses import dataclass
from typing import Literal

# How a trial's check ended. `none` is a check that gave no result at all.
type CheckEnd = Literal["passed", "failed", "timed_out", "unreadable_reward", "none"]


@dataclass(frozen=True, slots=True)
class RecordedTokens:
    """The token counts the job runner was given for a trial.

    `input` counts the cache reads too, so it is never below `cache_read`.
    """

    input: int
    cache_read: int
    output: int


@dataclass(frozen=True, slots=True)
class TrialFacts:
    """What the job runner recorded of one finished trial.

    `started_at` is an ISO 8601 time with its offset. `stopped_by` names
    what stopped the trial, or is empty, and is for a person to read: no
    code decides by it. `tokens` and `micro_dollars` are None when the job
    runner was not given them. `record` is the text of the trial record's
    file, or None when there is no file.
    """

    task: str
    runner_name: str
    started_at: str
    ms: int
    check: CheckEnd
    stopped_by: str
    task_folder_hash: str
    harness_version: str
    tokens: RecordedTokens | None
    micro_dollars: int | None
    record: str | None
    folder: str
