"""`bench run`: an experiment's two run sets and its decision, or a loose run set.

An experiment can be started again after any interruption. For each side
it skips the side whose run set is in the ledger, carries on the job of a
side whose `start` row is there, or writes the `start` row and only then
starts the job. So no trial of a side exists without its `start` row.
"""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from lamplighter.app.bench_lines import decision_lines, noise_floor_line, run_set_line
from lamplighter.app.bench_parts import (
    AS_ROOT,
    CHANGED_DURING,
    HELD_OUT,
    NO_KEY,
    NOT_CAPPED,
    BenchParts,
    LoadedSettings,
    ledger_rows,
    loaded,
    revision_fault,
    task_view,
)
from lamplighter.bench import (
    HARNESS,
    GateDecision,
    NotWhole,
    RunSet,
    RunSetPlan,
    compare,
    gate_rule,
    rule_hash,
    run_set,
)
from lamplighter.ports import (
    DecisionBody,
    DecisionRow,
    Failure,
    HarnessAgentSpec,
    Job,
    JsonObject,
    LedgerRow,
    PlanRow,
    RunnerAgentSpec,
    RunSetBody,
    RunSetRow,
    RunSetSide,
    Side,
    StartBody,
    StartRow,
    TrialFacts,
    from_plain,
    held_out_uses,
    to_plain,
    utc_text,
)

STALE_PLAN = "The plan was made for other tasks, code or rule. Plan a new experiment."
CAPPED = "LAMPLIGHTER_BENCH_KEY_IS_CAPPED"
# Harbor's own agents, each with the options of the bench's baselines.
BASELINES: dict[str, JsonObject] = {
    "terminus-2": {"max_turns": 40},
    "claude-code": {
        "version": "2.1.326",
        "max_turns": 40,
        "max_budget_usd": "1",
        "disable_web_search": True,
    },
}
_SIDES: tuple[Side, ...] = ("baseline", "candidate")


@dataclass(frozen=True, slots=True)
class _Chosen:
    tasks: tuple[str, ...]
    task_set_hash: str
    trials_per_task: int


@dataclass(frozen=True, slots=True)
class _Owner:
    # Whose run set it is: an experiment's side, or no one's for a loose one.
    experiment: str
    side: RunSetSide
    config_ids: tuple[str, ...]


async def run_experiment(parts: BenchParts, name: str) -> int:
    """Run the planned experiment's baseline, then its candidate, then the gate."""
    refused = _process_fault(parts, HARNESS)
    revision = parts.revision()
    refused = refused or revision_fault(revision, clean=True)
    if refused is not None:
        return parts.refuse(refused)
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    plan = _plan_of(rows, name)
    if plan is None:
        return parts.refuse(
            f'No plan named {name}. Run "lamplighter bench plan" first.'
        )
    stale = _stale(parts, plan, revision)
    if stale is not None:
        return parts.refuse(stale)
    for side in _SIDES:
        code = await _side(parts, plan, side, revision)
        if code != 0:
            return code
    return await _decide(parts, plan)


async def run_loose(
    parts: BenchParts,
    name: str,
    harness: str,
    tasks: Sequence[str],
    settings_file: Path,
    note: str,
) -> int:
    """Run one run set on the dev set, or on some of its tasks, outside any experiment.

    With chosen tasks, no row is written: the run set is not one of the
    whole dev set.
    """
    revision = parts.revision()
    refused = _process_fault(parts, harness) or revision_fault(revision, clean=False)
    if refused is not None:
        return parts.refuse(refused)
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    if any(_loose_name(r) == name for r in rows):
        return parts.refuse(f"A run set named {name} exists.")
    chosen = _dev_tasks(parts, tasks)
    if isinstance(chosen, str):
        return parts.refuse(chosen)
    settings = loaded(parts, settings_file)
    if isinstance(settings, str):
        return parts.refuse(settings)
    made = _loose_job(parts, name, harness, chosen, settings, revision)
    if isinstance(made, str):
        return parts.refuse(made)
    job, planned = made
    found = await _whole(parts, job, planned)
    if isinstance(found, int):
        return found
    if tasks:
        print("It ran on chosen tasks, so no row is written.", file=parts.out)
        return 0
    ids = (planned.portable_config_id,) if harness == HARNESS else ()
    return await _written(parts, job, *found, _Owner("", "", ids), note)


def _dev_tasks(parts: BenchParts, tasks: Sequence[str]) -> _Chosen | str:
    view = task_view(parts.root)
    if isinstance(view, Failure):
        return view.message
    rule = parts.rule()
    if isinstance(rule, Failure):
        return rule.message
    if view.split is None:
        return view.faults[0]
    chosen = tuple(tasks) or view.split.dev
    missing = next((task for task in chosen if task not in view.groups), None)
    if missing is not None:
        return f"There is no task named {missing}."
    if any(view.side(task) != "dev" for task in chosen):
        return HELD_OUT
    return _Chosen(chosen, view.hash, rule.trials_per_task)


def _loose_job(
    parts: BenchParts,
    name: str,
    harness: str,
    chosen: _Chosen,
    settings: LoadedSettings,
    revision: str,
) -> tuple[Job, RunSetPlan] | str:
    model = settings.config.model
    planned = RunSetPlan(
        run_set=name,
        harness=harness,
        portable_config_id="",
        code_revision=revision,
        task_set_hash=chosen.task_set_hash,
        split="dev",
        trials_per_task=chosen.trials_per_task,
        tasks=chosen.tasks,
        model=model.model,
        provider=model.adapter,
    )
    attempts = chosen.trials_per_task
    if harness != HARNESS:
        # A baseline installs itself, so its container has a network.
        agent = RunnerAgentSpec("runner", harness, model.model, BASELINES[harness])
        return Job(name, chosen.tasks, attempts, _pace(parts), True, agent), planned
    copy = _settings_copy(parts, name, settings)
    if isinstance(copy, str):
        return copy
    if copy.sha256 != settings.sha256:
        return f"The run set {name} was started with other settings, in {copy.path}."
    planned = dataclasses.replace(planned, portable_config_id=copy.portable_id)
    return _harness_job(parts, name, chosen.tasks, attempts, copy, revision), planned


def _process_fault(parts: BenchParts, harness: str) -> str | None:
    if not parts.environ.get("ANTHROPIC_API_KEY"):
        return NO_KEY
    if parts.user[0] == 0:
        return AS_ROOT
    if harness == "claude-code" and parts.environ.get(CAPPED) != "yes":
        return NOT_CAPPED
    return None


def _stale(parts: BenchParts, plan: PlanRow, revision: str) -> str | None:
    view = task_view(parts.root)
    if isinstance(view, Failure):
        return view.message
    rule = parts.rule()
    if isinstance(rule, Failure):
        return rule.message
    now = (view.hash, revision, rule_hash(rule))
    planned = (plan.body.task_set_hash, plan.code_revision, plan.body.rule_hash)
    return STALE_PLAN if now != planned else None


async def _side(parts: BenchParts, plan: PlanRow, side: Side, revision: str) -> int:
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    if any(isinstance(r, RunSetRow) and _of(r, plan, side) for r in rows):
        return 0
    name = f"{plan.experiment}--{side}"
    named = plan.body.baseline if side == "baseline" else plan.body.candidate
    planned_id = plan.config_ids[_SIDES.index(side)]
    source = loaded(parts, Path(named.path))
    copy = source if isinstance(source, str) else _settings_copy(parts, name, source)
    if isinstance(copy, str):
        return parts.refuse(copy)
    if (copy.sha256, copy.portable_id) != (named.sha256, planned_id):
        return parts.refuse(
            f"The {side}'s settings, {copy.path}, are not those the plan names."
            " Plan a new experiment."
        )
    if not any(isinstance(r, StartRow) and _of(r, plan, side) for r in rows):
        start = StartRow(
            kind="start",
            row_id=parts.ids.new_id(),
            at=utc_text(parts.clock.now()),
            experiment=plan.experiment,
            config_ids=(planned_id,),
            code_revision=revision,
            body=StartBody(side=side, run_set=name, split=plan.body.split),
        )
        failed = await parts.ledger.append(start)
        if failed is not None:
            return parts.refuse(failed.message)
    planned = RunSetPlan(
        run_set=name,
        harness=HARNESS,
        portable_config_id=planned_id,
        code_revision=revision,
        task_set_hash=plan.body.task_set_hash,
        split=plan.body.split,
        trials_per_task=plan.body.trials_per_task,
        tasks=plan.body.tasks,
        model=copy.config.model.model,
        provider=copy.config.model.adapter,
    )
    attempts = plan.body.trials_per_task
    job = _harness_job(parts, name, plan.body.tasks, attempts, copy, revision)
    found = await _whole(parts, job, planned)
    if isinstance(found, int):
        return found
    owner = _Owner(plan.experiment, side, (planned_id,))
    return await _written(parts, job, *found, owner, "")


async def _whole(
    parts: BenchParts, job: Job, planned: RunSetPlan
) -> tuple[RunSet, Sequence[TrialFacts]] | int:
    # The job names the code it was planned on, and the checkout must be
    # that code before the job and after it.
    if parts.revision() != planned.code_revision:
        return parts.refuse(CHANGED_DURING)
    failed = await parts.jobs.run(job)
    if failed is not None:
        return parts.refuse(failed.message)
    if parts.revision() != planned.code_revision:
        return parts.refuse(CHANGED_DURING)
    found = await parts.jobs.trials(job.run_set)
    if isinstance(found, Failure):
        return parts.refuse(found.message)
    built = run_set(planned, found)
    if isinstance(built, NotWhole):
        return _not_whole(parts, built, planned)
    print(f"The run set {run_set_line(built)}.", file=parts.out)
    return built, found


async def _written(
    parts: BenchParts,
    job: Job,
    built: RunSet,
    found: Sequence[TrialFacts],
    owner: _Owner,
    note: str,
) -> int:
    started, ended = _span(found)
    row = RunSetRow(
        kind="run_set",
        row_id=parts.ids.new_id(),
        at=utc_text(parts.clock.now()),
        experiment=owner.experiment,
        config_ids=owner.config_ids,
        code_revision=built.code_revision,
        body=RunSetBody(
            run_set=_object(built),
            side=owner.side,
            totals=_totals(built),
            started_at=started,
            ended_at=ended,
            concurrent_trials=job.concurrent_trials,
            note=note,
        ),
    )
    failed = await parts.ledger.append(row)
    return 0 if failed is None else parts.refuse(failed.message)


async def _decide(parts: BenchParts, plan: PlanRow) -> int:
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    found = next(
        (
            r
            for r in rows
            if isinstance(r, DecisionRow) and r.experiment == plan.experiment
        ),
        None,
    )
    if found is None:
        decided = _decision(rows, plan)
        if isinstance(decided, Failure):
            return parts.refuse(decided.message)
        found = DecisionRow(
            kind="decision",
            row_id=parts.ids.new_id(),
            at=utc_text(parts.clock.now()),
            experiment=plan.experiment,
            config_ids=plan.config_ids,
            code_revision=parts.revision(),
            body=DecisionBody(
                decision=_object(decided),
                held_out_uses=held_out_uses(rows),
                same_config=plan.config_ids[0] == plan.config_ids[1],
            ),
        )
        failed = await parts.ledger.append(found)
        if failed is not None:
            return parts.refuse(failed.message)
        rows.append(found)
    for line in decision_lines(from_plain(GateDecision, found.body.decision)):
        print(line, file=parts.out)
    print(noise_floor_line(rows), file=parts.out)
    return 0


def _decision(rows: list[LedgerRow], plan: PlanRow) -> GateDecision | Failure:
    # The run sets are read back from the ledger, so what is compared is
    # exactly what was recorded.
    sides = {
        r.body.side: from_plain(RunSet, r.body.run_set)
        for r in rows
        if isinstance(r, RunSetRow) and r.experiment == plan.experiment
    }
    rule = gate_rule(plan.body.rule)
    if isinstance(rule, Failure):
        return rule
    return compare(sides["baseline"], sides["candidate"], rule)


def _settings_copy(
    parts: BenchParts, run_set_name: str, source: LoadedSettings
) -> LoadedSettings | str:
    # Made once, from the file as it is then. A copy that is there is kept,
    # so a job that is carried on runs on the settings it started with.
    copy = parts.run_sets() / run_set_name / "settings.toml"
    try:
        if not copy.exists():
            copy.parent.mkdir(parents=True, exist_ok=True)
            temporary = copy.with_name(f"{copy.name}.new")
            temporary.write_bytes(source.path.read_bytes())
            temporary.replace(copy)
    except OSError as error:
        return f"The settings copy {copy} could not be made: {error.strerror}."
    return loaded(parts, copy)


def _harness_job(
    parts: BenchParts,
    name: str,
    tasks: tuple[str, ...],
    attempts: int,
    copy: LoadedSettings,
    revision: str,
) -> Job:
    agent = HarnessAgentSpec(
        kind="harness",
        settings=str(copy.path),
        shell_path=parts.settings.shell_path,
        budget_micro_dollars=parts.settings.run_set_max_micro_dollars,
        portable_config_id=copy.portable_id,
        code_revision=revision,
        run_as=parts.user,
    )
    return Job(name, tasks, attempts, _pace(parts), False, agent)


def _pace(parts: BenchParts) -> int:
    return parts.settings.concurrent_trials


def _not_whole(parts: BenchParts, found: NotWhole, planned: RunSetPlan) -> int:
    for count in found.counts:
        print(
            f"{count.task}: {count.found} of {planned.trials_per_task} trials"
            " have a result.",
            file=parts.errors,
        )
    if found.versions:
        print(
            f"The trials ran on more than one harness: {', '.join(found.versions)}.",
            file=parts.errors,
        )
    return parts.refuse(
        f"The run set {planned.run_set} is not whole. Run the same command again"
        " to carry on with the trials that are missing."
    )


def _plan_of(rows: Sequence[LedgerRow], name: str) -> PlanRow | None:
    return next(
        (r for r in rows if isinstance(r, PlanRow) and r.experiment == name), None
    )


def _of(row: StartRow | RunSetRow, plan: PlanRow, side: Side) -> bool:
    return row.experiment == plan.experiment and row.body.side == side


def _loose_name(row: LedgerRow) -> object:
    if isinstance(row, RunSetRow) and not row.experiment:
        return row.body.run_set.get("run_set")
    return None


def _span(found: Sequence[TrialFacts]) -> tuple[str, str]:
    starts = [datetime.fromisoformat(f.started_at) for f in found]
    ends = [
        s + timedelta(milliseconds=f.ms) for s, f in zip(starts, found, strict=True)
    ]
    return utc_text(min(starts)), utc_text(max(ends))


def _totals(built: RunSet) -> JsonObject:
    trials = built.trials
    priced = [t.micro_dollars for t in trials if t.micro_dollars is not None]
    return {
        "trials": len(trials),
        "pass": sum(t.outcome == "pass" for t in trials),
        "fail": sum(t.outcome == "fail" for t in trials),
        "infra": sum(t.outcome == "infra" for t in trials),
        "micro_dollars": sum(priced),
        "unpriced_trials": len(trials) - len(priced),
        "input_uncached": sum(t.tokens.uncached for t in trials),
        "cache_read": sum(t.tokens.read for t in trials),
        "cache_write": sum(t.tokens.written for t in trials),
        "output": sum(t.tokens.output for t in trials),
        "ms": sum(t.ms for t in trials),
    }


def _object(value: object) -> JsonObject:
    plain = to_plain(value)
    assert isinstance(plain, dict)
    return plain
