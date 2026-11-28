"""The port to the job runner of the bench, and the records that cross it."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

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


@dataclass(frozen=True, slots=True)
class HarnessAgentSpec:
    """This repo's harness as the agent of a job.

    `settings` is the run set's settings copy. The run set stops starting
    model calls once its trial records add up to `budget_micro_dollars`.
    A trial on another portable config id or code revision than the two
    planned ones does not count. Commands run as `run_as`, a user id and a
    group id.
    """

    kind: Literal["harness"]
    settings: str
    shell_path: str
    budget_micro_dollars: int
    portable_config_id: str
    code_revision: str
    run_as: tuple[int, int]


@dataclass(frozen=True, slots=True)
class RunnerAgentSpec:
    """One of the job runner's own agents, by name.

    `model` is None for an agent that calls no model, such as the reference
    solution. `options` are the agent's own.
    """

    kind: Literal["runner"]
    name: str
    model: str | None
    options: JsonObject


@dataclass(frozen=True, slots=True)
class ReferenceAgentSpec:
    """The task's reference solution, run the way a trial's commands run.

    It runs in a work folder seeded as the harness's is, as `run_as`, a
    user id and a group id, so a task that only root could do fails its
    check.
    """

    kind: Literal["reference"]
    run_as: tuple[int, int]


type AgentSpec = HarnessAgentSpec | RunnerAgentSpec | ReferenceAgentSpec


@dataclass(frozen=True, slots=True)
class Job:
    """What one run set asks the job runner to run.

    `network` says whether the task's container has a network: only a
    baseline harness that installs itself needs one.
    """

    run_set: str
    tasks: tuple[str, ...]
    attempts: int
    concurrent_trials: int
    network: bool
    agent: AgentSpec


class BenchJobs(Protocol):
    """The job runner. It knows the tasks and the trials, and nothing of the gate.

    `run` starts a job, or carries on the job of the same run set, and
    returns when the runner has ended. A finished trial is never run
    again. A trial that was cut off, or that the runner recorded as
    cancelled, is run again as a new trial, after the containers and
    networks that cut-off trials of this job left are removed, and no
    others. `run` gives None when the runner ended well, which says
    nothing about the trials; `unavailable` when Docker does not answer,
    the runner could not start, or it ended any other way; and `conflict`
    when the run set was started as another job, or another `run` of it
    is still going. The trials at a time and the budget are not part of
    what makes a job another one. `trials` gives one entry per finished
    trial of a run set and changes no file.
    """

    async def run(self, job: Job) -> Failure | None:
        """Run the job to its end, or carry it on."""
        ...

    async def trials(self, run_set: str) -> Sequence[TrialFacts] | Failure:
        """Give what the runner recorded of each finished trial."""
        ...
