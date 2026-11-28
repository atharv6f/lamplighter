"""The gate: whether a candidate run set beats the baseline, decided exactly."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.bench.gate_rule import GateRule, rule_hash
from lamplighter.bench.interval import T_THOUSANDTHS, half_width_bp, mean_bp
from lamplighter.bench.run_set import HARNESS, RunSet, TrialResult
from lamplighter.bench.sign_flip import sign_counts
from lamplighter.ports import Failure

SOURCE = "gate"
# The interval's table ends at 29 degrees of freedom.
MOST_TASKS = len(T_THOUSANDTHS) + 1
COST_GUARD = "cost_per_valid_trial"

type GateOutcome = Literal["keep", "reject", "inconclusive"]
type GateWhy = Literal[
    "better",
    "worse",
    "no_clear_difference",
    "guard_failed",
    "budget_reached",
    "infra_over_limit",
    "task_without_valid_trial",
]


@dataclass(frozen=True, slots=True)
class SideSummary:
    """One side's counts.

    A task counts as all passed when its passes equal the planned trials,
    so an infra trial keeps it out. The cost for each valid trial is
    rounded down, and is None when a valid trial's cost is unknown or the
    side has no valid trial. `mean_pass_bp` is None when the gate stopped
    before its test.
    """

    passes: int
    fails: int
    infra: int
    planned: int
    mean_pass_bp: int | None
    tasks_all_passed: int
    tasks_any_passed: int
    micro_dollars_per_valid_trial: int | None


@dataclass(frozen=True, slots=True)
class GuardResult:
    """Whether a guard passed, and what it compared."""

    name: str
    passed: bool
    baseline_micro_dollars_per_valid_trial: int | None
    candidate_micro_dollars_per_valid_trial: int | None


@dataclass(frozen=True, slots=True)
class ChangedTask:
    """A task whose pass fraction differs, with each side's passes and valid trials."""

    task: str
    baseline_passes: int
    baseline_valid: int
    candidate_passes: int
    candidate_valid: int


@dataclass(frozen=True, slots=True)
class GateDecision:
    """What the gate decided, and every number it decided by.

    Differences are in hundredths of a point. The p-values are in
    millionths, rounded up, so a printed value at or under alpha always
    goes with a keep or a reject. The interval is a reading aid and decides
    nothing. When the gate stopped before its test, the fields of the test
    are None and `guards` and `changed` are empty.
    """

    outcome: GateOutcome
    why: GateWhy
    rule_hash: str
    tasks: int
    tasks_up: int | None
    tasks_down: int | None
    tasks_same: int | None
    difference_bp: int | None
    interval_low_bp: int | None
    interval_high_bp: int | None
    p_better_ppm: int | None
    p_worse_ppm: int | None
    baseline: SideSummary
    candidate: SideSummary
    guards: tuple[GuardResult, ...]
    changed: tuple[ChangedTask, ...]


@dataclass(frozen=True, slots=True)
class _Task:
    passes: int
    valid: int

    @property
    def sixths(self) -> int:
        # 1, 2 or 3 valid trials divide six, so the score is whole.
        return 6 * self.passes // self.valid


@dataclass(frozen=True, slots=True)
class _Side:
    tasks: dict[str, _Task]
    passes: int
    fails: int
    infra: int
    planned: int
    budget_reached: bool
    # Added over valid trials. None when a valid trial's cost is unknown.
    cost: int | None

    @property
    def valid(self) -> int:
        return self.passes + self.fails

    @property
    def per_valid_trial(self) -> int | None:
        return None if self.cost is None or not self.valid else self.cost // self.valid


def compare(
    baseline: RunSet, candidate: RunSet, rule: GateRule
) -> GateDecision | Failure:
    """Decide whether the candidate is better, worse, or neither, by the rule.

    It reads no file, no clock and no random number, and every number in it
    is whole, so the same inputs give the same bytes on any machine. Two run
    sets that cannot be compared give `invalid_input` and never a decision.
    A guard only ever turns a keep into a reject.
    """
    refused = _refused(baseline, candidate, rule)
    if refused is not None:
        return Failure(
            kind="invalid_input", effect="none", message=refused, source=SOURCE
        )
    base = _side(baseline)
    cand = _side(candidate)
    stopped = _stopped_why(base, cand, rule)
    if stopped is not None:
        return _stopped(stopped, base, cand, rule)
    return _tested(base, cand, rule)


def _refused(baseline: RunSet, candidate: RunSet, rule: GateRule) -> str | None:
    differ = [
        name
        for name, one, other in (
            ("task set hash", baseline.task_set_hash, candidate.task_set_hash),
            ("code revision", baseline.code_revision, candidate.code_revision),
            ("split", baseline.split, candidate.split),
            ("trials per task", baseline.trials_per_task, candidate.trials_per_task),
        )
        if one != other
    ]
    if differ:
        return f"The two run sets differ in their {', '.join(differ)}."
    if baseline.trials_per_task != rule.trials_per_task:
        return "The run sets' trials per task are not the gate rule's."
    if baseline.harness != HARNESS or candidate.harness != HARNESS:
        return f"Only run sets of {HARNESS} can be compared."
    tasks = {t.task for t in baseline.trials}
    if tasks != {t.task for t in candidate.trials}:
        return "The two run sets are not on the same tasks."
    if not 1 <= len(tasks) <= MOST_TASKS:
        return f"The gate compares 1 to {MOST_TASKS} tasks."
    for run_set in (baseline, candidate):
        broken = _broken_trials(run_set.trials, run_set.trials_per_task)
        if broken is not None:
            return f"{run_set.run_set}: {broken}"
    return None


def _broken_trials(trials: Sequence[TrialResult], per_task: int) -> str | None:
    attempts: dict[str, list[int]] = {}
    for trial in trials:
        if trial.outcome not in ("pass", "fail", "infra"):
            return f"a trial of {trial.task} has the outcome {trial.outcome!r}."
        attempts.setdefault(trial.task, []).append(trial.attempt)
    for task, numbers in sorted(attempts.items()):
        if sorted(numbers) != list(range(1, per_task + 1)):
            return f"the attempts of {task} are not 1 to {per_task}, each once."
    return None


def _side(run_set: RunSet) -> _Side:
    tasks: dict[str, _Task] = {}
    cost: int | None = 0
    for trial in run_set.trials:
        task = tasks.get(trial.task, _Task(0, 0))
        if trial.outcome == "infra":
            tasks[trial.task] = task
            continue
        tasks[trial.task] = _Task(
            task.passes + int(trial.outcome == "pass"), task.valid + 1
        )
        known = trial.micro_dollars
        cost = None if cost is None or known is None else cost + known
    outcomes = [t.outcome for t in run_set.trials]
    return _Side(
        tasks=tasks,
        passes=outcomes.count("pass"),
        fails=outcomes.count("fail"),
        infra=outcomes.count("infra"),
        planned=len(tasks) * run_set.trials_per_task,
        budget_reached=any(t.reason == "budget_reached" for t in run_set.trials),
        cost=cost,
    )


def _stopped_why(base: _Side, cand: _Side, rule: GateRule) -> GateWhy | None:
    sides = (base, cand)
    # A run set that hit its money stop lost its last trials, not a fair draw.
    if any(side.budget_reached for side in sides):
        return "budget_reached"
    if any(s.infra * 1000 > rule.infra_limit_permille * s.planned for s in sides):
        return "infra_over_limit"
    if any(not task.valid for side in sides for task in side.tasks.values()):
        return "task_without_valid_trial"
    return None


def _stopped(why: GateWhy, base: _Side, cand: _Side, rule: GateRule) -> GateDecision:
    return GateDecision(
        outcome="inconclusive",
        why=why,
        rule_hash=rule_hash(rule),
        tasks=len(base.tasks),
        tasks_up=None,
        tasks_down=None,
        tasks_same=None,
        difference_bp=None,
        interval_low_bp=None,
        interval_high_bp=None,
        p_better_ppm=None,
        p_worse_ppm=None,
        baseline=_summary(base, rule, None),
        candidate=_summary(cand, rule, None),
        guards=(),
        changed=(),
    )


def _tested(base: _Side, cand: _Side, rule: GateRule) -> GateDecision:
    names = sorted(base.tasks)
    sixths = [cand.tasks[n].sixths - base.tasks[n].sixths for n in names]
    counts = sign_counts(sixths)
    better = 2 * counts.above + counts.at
    worse = 2 * counts.below + counts.at
    patterns = 2 * counts.total
    outcome, why = _decided(better, worse, patterns, rule.alpha_permille)
    guard = _cost_guard(base, cand, rule)
    if outcome == "keep" and not guard.passed:
        outcome, why = "reject", "guard_failed"
    difference = mean_bp(sixths)
    half = half_width_bp(sixths)
    return GateDecision(
        outcome=outcome,
        why=why,
        rule_hash=rule_hash(rule),
        tasks=len(names),
        tasks_up=sum(d > 0 for d in sixths),
        tasks_down=sum(d < 0 for d in sixths),
        tasks_same=sum(d == 0 for d in sixths),
        difference_bp=difference,
        interval_low_bp=None if half is None else difference - half,
        interval_high_bp=None if half is None else difference + half,
        p_better_ppm=_ppm_up(better, patterns),
        p_worse_ppm=_ppm_up(worse, patterns),
        baseline=_summary(base, rule, _mean_pass(base)),
        candidate=_summary(cand, rule, _mean_pass(cand)),
        guards=(guard,),
        changed=tuple(
            _changed(n, base.tasks[n], cand.tasks[n])
            for n, d in zip(names, sixths, strict=True)
            if d
        ),
    )


def _decided(
    better: int, worse: int, patterns: int, alpha_permille: int
) -> tuple[GateOutcome, GateWhy]:
    # p_better is better / patterns, and p_worse is worse / patterns.
    if 1000 * better <= alpha_permille * patterns:
        return "keep", "better"
    if 1000 * worse <= alpha_permille * patterns:
        return "reject", "worse"
    return "inconclusive", "no_clear_difference"


def _cost_guard(base: _Side, cand: _Side, rule: GateRule) -> GuardResult:
    # A cost that cannot be measured fails the guard.
    passed = (
        base.cost is not None
        and cand.cost is not None
        and cand.cost * base.valid * 1000
        <= base.cost * cand.valid * rule.guards.cost_per_valid_trial_permille
    )
    return GuardResult(
        name=COST_GUARD,
        passed=passed,
        baseline_micro_dollars_per_valid_trial=base.per_valid_trial,
        candidate_micro_dollars_per_valid_trial=cand.per_valid_trial,
    )


def _mean_pass(side: _Side) -> int:
    return mean_bp([task.sixths for task in side.tasks.values()])


def _summary(side: _Side, rule: GateRule, mean_pass_bp: int | None) -> SideSummary:
    tasks = side.tasks.values()
    return SideSummary(
        passes=side.passes,
        fails=side.fails,
        infra=side.infra,
        planned=side.planned,
        mean_pass_bp=mean_pass_bp,
        tasks_all_passed=sum(t.passes == rule.trials_per_task for t in tasks),
        tasks_any_passed=sum(t.passes > 0 for t in tasks),
        micro_dollars_per_valid_trial=side.per_valid_trial,
    )


def _changed(name: str, base: _Task, cand: _Task) -> ChangedTask:
    return ChangedTask(name, base.passes, base.valid, cand.passes, cand.valid)


def _ppm_up(count: int, patterns: int) -> int:
    return -(-count * 1_000_000 // patterns)
