"""A run set: every planned trial of one harness on a set of tasks, with outcomes."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from lamplighter.bench.outcome import (
    OutcomeKind,
    OutcomeReason,
    TrialOutcome,
    check_outcome,
    harness_outcome,
)
from lamplighter.bench.trial_record import Tokens, TrialRecord, read_record
from lamplighter.ports import RecordedTokens, TrialFacts

# The name a run set gives this repo's own harness. Any other is a baseline.
HARNESS = "lamplighter"


@dataclass(frozen=True, slots=True)
class ConfigModel:
    """One model entry of a run set's settings: its name and the model it runs."""

    name: str
    model: str


@dataclass(frozen=True, slots=True)
class ConfigLadder:
    """One route of a run set's settings: its id and its ladder, weakest first."""

    route: str
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunSetPlan:
    """What a run set was planned with.

    `portable_config_id` is empty for a baseline harness, and `models` and
    `ladders`, read from the settings this repo's harness runs on, are
    empty for one.
    """

    run_set: str
    harness: str
    portable_config_id: str
    code_revision: str
    task_set_hash: str
    split: str
    trials_per_task: int
    tasks: tuple[str, ...]
    model: str
    provider: str
    models: tuple[ConfigModel, ...]
    ladders: tuple[ConfigLadder, ...]


@dataclass(frozen=True, slots=True)
class TrialResult:
    """One trial of a run set.

    The counts and the run's ending are the trial record's. A trial with no
    record that can be read has no ending, no steps and no tool calls, and
    the tokens and cost the job runner was given, which is how a baseline
    harness is counted. `ms` is the job runner's, on every harness.
    `models_called` and `escalated` are the record's, and none named and
    false with no record. A result kept before they were has the same.
    """

    task: str
    attempt: int
    outcome: OutcomeKind
    reason: OutcomeReason | None
    run_reason: str | None
    steps: int
    tool_calls: int
    tokens: Tokens
    micro_dollars: int | None
    ms: int
    runner_name: str
    task_folder_hash: str
    models_called: tuple[str, ...] = ()
    escalated: bool = False


@dataclass(frozen=True, slots=True)
class RunSet:
    """A whole run set: each planned task has exactly its trials.

    `models` and `ladders` are the plan's. A run set kept before they were
    reads with none.
    """

    run_set: str
    harness: str
    harness_version: str
    portable_config_id: str
    code_revision: str
    task_set_hash: str
    split: str
    trials_per_task: int
    model: str
    provider: str
    trials: tuple[TrialResult, ...]
    models: tuple[ConfigModel, ...] = ()
    ladders: tuple[ConfigLadder, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskCount:
    """A task and how many trials of it were found."""

    task: str
    found: int


@dataclass(frozen=True, slots=True)
class NotWhole:
    """Why the trials found do not make the planned run set.

    `counts` has every task, planned or not, whose count of trials is not
    the planned one, in the order of the names. `versions` has the harness
    versions found when there is not exactly one.
    """

    counts: tuple[TaskCount, ...]
    versions: tuple[str, ...]


def run_set(plan: RunSetPlan, found: Iterable[TrialFacts]) -> RunSet | NotWhole:
    """Give the run set of the trials found, or why they do not make it whole.

    A task's trials are numbered 1, 2 and so on by their start, compared
    as points in time, and then by the job runner's name for them.
    """
    by_task: dict[str, list[TrialFacts]] = {task: [] for task in plan.tasks}
    for facts in found:
        by_task.setdefault(facts.task, []).append(facts)
    counts = tuple(
        TaskCount(task, len(trials))
        for task, trials in sorted(by_task.items())
        if len(trials) != plan.trials_per_task or task not in plan.tasks
    )
    versions = sorted(
        {f.harness_version for trials in by_task.values() for f in trials}
    )
    if counts or len(versions) != 1:
        return NotWhole(counts, tuple(versions) if len(versions) != 1 else ())
    return RunSet(
        run_set=plan.run_set,
        harness=plan.harness,
        harness_version=versions[0],
        portable_config_id=plan.portable_config_id,
        code_revision=plan.code_revision,
        task_set_hash=plan.task_set_hash,
        split=plan.split,
        trials_per_task=plan.trials_per_task,
        model=plan.model,
        provider=plan.provider,
        models=plan.models,
        ladders=plan.ladders,
        trials=tuple(
            _result(plan.harness, attempt, facts)
            for _, trials in sorted(by_task.items())
            for attempt, facts in enumerate(sorted(trials, key=_order), start=1)
        ),
    )


def _order(facts: TrialFacts) -> tuple[datetime, str]:
    return datetime.fromisoformat(facts.started_at), facts.runner_name


def _result(harness: str, attempt: int, facts: TrialFacts) -> TrialResult:
    if harness != HARNESS:
        return _numbered(attempt, facts, check_outcome(facts.check), None)
    record = None if facts.record is None else read_record(facts.record)
    return _numbered(attempt, facts, harness_outcome(record, facts.check), record)


def _numbered(
    attempt: int, facts: TrialFacts, outcome: TrialOutcome, record: TrialRecord | None
) -> TrialResult:
    return TrialResult(
        task=facts.task,
        attempt=attempt,
        outcome=outcome.kind,
        reason=outcome.reason,
        run_reason=None if record is None else record.run_reason,
        steps=0 if record is None else record.steps,
        tool_calls=0 if record is None else record.tool_calls,
        tokens=_counted(facts.tokens) if record is None else record.tokens,
        micro_dollars=facts.micro_dollars if record is None else record.micro_dollars,
        ms=facts.ms,
        runner_name=facts.runner_name,
        task_folder_hash=facts.task_folder_hash,
        models_called=() if record is None else record.models_called,
        escalated=False if record is None else record.escalated,
    )


def _counted(tokens: RecordedTokens | None) -> Tokens:
    # The job runner's input count holds the cache reads and the cache
    # writes, and says nothing of the writes alone, so they are counted as
    # uncached.
    if tokens is None:
        return Tokens(0, 0, 0, 0)
    return Tokens(tokens.input - tokens.cache_read, tokens.cache_read, 0, tokens.output)
