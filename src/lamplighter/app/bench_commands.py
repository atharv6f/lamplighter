"""The parts of `lamplighter bench` beside `run`: tasks, check, plan, ledger and show.

Each part is a function of the parts it is given and gives the exit code:
0 when it did its job, 1 when it refused or a check failed.
"""

import argparse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from lamplighter.app.bench_lines import ledger_line, noise_floor_line
from lamplighter.app.bench_parts import (
    AS_ROOT,
    BenchParts,
    LoadedSettings,
    ledger_rows,
    loaded,
    revision_fault,
    task_view,
)
from lamplighter.app.bench_run import BASELINES, run_experiment, run_loose
from lamplighter.app.bench_show import transcript_lines
from lamplighter.app.task_set_view import TaskSetView
from lamplighter.bench import (
    HARNESS,
    GateRule,
    Split,
    harness_outcome,
    read_record,
    rule_hash,
)
from lamplighter.ports import (
    AgentSpec,
    CorrectionBody,
    CorrectionRow,
    DecisionRow,
    Failure,
    Job,
    LedgerRow,
    PlanBody,
    PlanRow,
    ReferenceAgentSpec,
    RunnerAgentSpec,
    RunSetRow,
    SettingsFile,
    SplitName,
    TrialFacts,
    row_text,
    to_plain,
    utc_text,
)

ORACLE_ATTEMPTS = 3


def add_bench_parts(bench: argparse.ArgumentParser) -> None:
    """Add the six parts of `bench` to its parser."""
    parts = bench.add_subparsers(dest="action", required=True)
    parts.add_parser("tasks", help="list and check the tasks and the split")
    parts.add_parser(
        "check", help="run the reference solution and nothing on each task"
    )
    plan = parts.add_parser("plan", help="plan an experiment against these settings")
    plan.add_argument("name")
    plan.add_argument("--candidate", type=Path, required=True)
    plan.add_argument("--split", choices=("dev", "held_out"), default="held_out")
    plan.add_argument("--note", default="")
    run = parts.add_parser("run", help="run a planned experiment, or a loose run set")
    which = run.add_mutually_exclusive_group(required=True)
    which.add_argument("name", nargs="?")
    which.add_argument("--experiment")
    run.add_argument("--harness", choices=(HARNESS, *BASELINES), default=HARNESS)
    run.add_argument("--task", action="append", default=[])
    run.add_argument("--note", default="")
    ledger = parts.add_parser("ledger", help="print the ledger, or correct a row")
    ledger.add_argument("--experiment")
    ledger.add_argument("--correct", metavar="ROW")
    ledger.add_argument("--note")
    show = parts.add_parser("show", help="print one trial's transcript")
    show.add_argument("run_set")
    show.add_argument("task")
    show.add_argument("--attempt", type=int, default=1)


async def bench_command(
    parts: BenchParts, args: argparse.Namespace, settings_file: Path
) -> int:
    """Run the part of `bench` the command line names.

    `settings_file` is the settings file the command line was given: the
    baseline of a plan, and the harness of a loose run set.
    """
    match args.action:
        case "tasks":
            return list_tasks(parts)
        case "check":
            return await check_tasks(parts)
        case "plan":
            split: SplitName = args.split
            return await plan_experiment(
                parts, args.name, settings_file, args.candidate, split, args.note
            )
        case "run":
            return await _run(parts, args, settings_file)
        case "ledger":
            return await _ledger(parts, args)
        case _:
            return await show_trial(parts, args.run_set, args.task, args.attempt)


def list_tasks(parts: BenchParts) -> int:
    """Print each task with its group and side, every fault, and the task set hash."""
    view = task_view(parts.root)
    if isinstance(view, Failure):
        return parts.refuse(view.message)
    for task, group in view.groups.items():
        print(f"{task}  {group or '?'}  {view.side(task) or '?'}", file=parts.out)
    print(f"task set hash: {view.hash}", file=parts.out)
    for fault in view.faults:
        print(fault, file=parts.errors)
    return 1 if view.faults else 0


async def plan_experiment(
    parts: BenchParts,
    name: str,
    baseline: Path,
    candidate: Path,
    split: SplitName,
    note: str,
) -> int:
    """Write the plan of an experiment: its rule, tasks and two settings files."""
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    if any(_names(row, name) for row in rows):
        return parts.refuse(f"The name {name} is taken.")
    revision = parts.revision()
    inputs = _plan_inputs(parts, revision, baseline, candidate)
    if isinstance(inputs, str):
        return parts.refuse(inputs)
    base, cand, rule, view, found = inputs
    tasks = found.dev if split == "dev" else found.held_out
    plain_rule = to_plain(rule)
    assert isinstance(plain_rule, dict)
    row = PlanRow(
        kind="plan",
        row_id=parts.ids.new_id(),
        at=utc_text(parts.clock.now()),
        experiment=name,
        config_ids=(base.portable_id, cand.portable_id),
        code_revision=revision,
        body=PlanBody(
            rule=plain_rule,
            rule_hash=rule_hash(rule),
            split=split,
            task_set_hash=view.hash,
            tasks=tasks,
            trials_per_task=rule.trials_per_task,
            baseline=SettingsFile(str(base.path.resolve()), base.sha256),
            candidate=SettingsFile(str(cand.path.resolve()), cand.sha256),
            harbor_version=parts.harbor_version,
            note=note,
        ),
    )
    failed = await parts.ledger.append(row)
    if failed is not None:
        return parts.refuse(failed.message)
    print(
        f"Planned {name}: {len(tasks)} tasks on the {split} set,"
        f" {rule.trials_per_task} trials each.",
        file=parts.out,
    )
    if base.portable_id == cand.portable_id:
        print("The two configs are the same: it is an A/A experiment.", file=parts.out)
    return 0


async def show_ledger(parts: BenchParts, experiment: str | None) -> int:
    """Print one line per row, or one experiment's rows in full with the noise floor."""
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    if experiment is None:
        for row in rows:
            print(ledger_line(row), file=parts.out)
        return 0
    chosen = [row for row in rows if row.experiment == experiment]
    if not chosen:
        return parts.refuse(f"The ledger has no row of {experiment}.")
    for row in chosen:
        print(row_text(row), file=parts.out)
        if isinstance(row, DecisionRow):
            print(noise_floor_line(rows), file=parts.out)
    return 0


async def correct_row(parts: BenchParts, row_id: str, note: str) -> int:
    """Add a note about a row. It changes nothing any rule reads."""
    rows = await ledger_rows(parts)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    target = next((row for row in rows if row.row_id == row_id), None)
    if target is None:
        return parts.refuse(f"The ledger has no row {row_id}.")
    row = CorrectionRow(
        kind="correction",
        row_id=parts.ids.new_id(),
        at=utc_text(parts.clock.now()),
        experiment=target.experiment,
        config_ids=(),
        code_revision=parts.revision(),
        body=CorrectionBody(corrects=row_id, note=note),
    )
    failed = await parts.ledger.append(row)
    if failed is not None:
        return parts.refuse(failed.message)
    print(f"Added the correction {row.row_id} of {row_id}.", file=parts.out)
    return 0


async def check_tasks(parts: BenchParts) -> int:
    """Run the reference solution 3 times and nothing once on every task.

    The reference solution runs as this process's user, as a trial's
    commands do, so a task that only root could do breaks the rule. Each
    run has job folders of its own, named with a new id, so an old result
    is never read as a new one. No model sees a task, so no row is written
    and it is not a use of the held-out set.
    """
    if parts.user[0] == 0:
        return parts.refuse(AS_ROOT)
    view = task_view(parts.root)
    if isinstance(view, Failure):
        return parts.refuse(view.message)
    tasks = tuple(view.groups)
    if not tasks:
        return parts.refuse("There is no task to check.")
    stamp = parts.ids.new_id()
    found: list[Sequence[TrialFacts]] = []
    runs: tuple[tuple[str, AgentSpec, int], ...] = (
        ("reference", ReferenceAgentSpec("reference", parts.user), ORACLE_ATTEMPTS),
        ("nop", RunnerAgentSpec("runner", "nop", None, {}), 1),
    )
    for name, spec, attempts in runs:
        pace = parts.settings.concurrent_trials
        job = Job(f"{stamp}-{name}", tasks, attempts, pace, False, spec)
        failed = await parts.check_jobs.run(job)
        if failed is not None:
            return parts.refuse(failed.message)
        trials = await parts.check_jobs.trials(job.run_set)
        if isinstance(trials, Failure):
            return parts.refuse(trials.message)
        found.append(trials)
    oracle, nop = found
    broken = 0
    for task in tasks:
        faults = _check_faults(task, oracle, nop)
        broken += bool(faults)
        for line in faults:
            print(line, file=parts.errors)
    print(f"{len(tasks) - broken} of {len(tasks)} tasks kept the rule.", file=parts.out)
    return 1 if broken else 0


async def show_trial(parts: BenchParts, run_set: str, task: str, attempt: int) -> int:
    """Print one trial's transcript from its export, and its outcome."""
    found = await parts.jobs.trials(run_set)
    if isinstance(found, Failure):
        return parts.refuse(found.message)
    of_task = sorted((f for f in found if f.task == task), key=_started)
    if not 1 <= attempt <= len(of_task):
        return parts.refuse(f"{run_set} has no trial {attempt} of {task}.")
    facts = of_task[attempt - 1]
    folder = Path(facts.folder)
    events = folder / "harness" / "export" / "events.jsonl"
    try:
        lines = transcript_lines(events.read_text(encoding="utf-8"))
    except FileNotFoundError:
        lines = [
            f"{folder} has no export of this harness. Harbor keeps an agent's own"
            f" log in {folder / 'agent'}."
        ]
    except OSError as error:
        return parts.refuse(f"{events} could not be read: {error.strerror}.")
    for line in lines:
        print(line, file=parts.out)
    record = None if facts.record is None else read_record(facts.record)
    if record is None:
        print(f"No trial record. The check ended: {facts.check}.", file=parts.out)
        return 0
    outcome = harness_outcome(record, facts.check)
    reason = "" if outcome.reason is None else f", {outcome.reason}"
    print(f"Outcome: {outcome.kind}{reason}.", file=parts.out)
    return 0


async def _run(parts: BenchParts, args: argparse.Namespace, settings_file: Path) -> int:
    if args.experiment is None:
        harness: str = args.harness
        return await run_loose(
            parts, args.name, harness, args.task, settings_file, args.note
        )
    if args.harness != HARNESS or args.task or args.note:
        return parts.refuse("--harness, --task and --note are for a loose run set.")
    return await run_experiment(parts, args.experiment)


async def _ledger(parts: BenchParts, args: argparse.Namespace) -> int:
    if args.correct is None:
        if args.note is not None:
            return parts.refuse("--note goes with --correct.")
        return await show_ledger(parts, args.experiment)
    if args.note is None or args.experiment is not None:
        return parts.refuse("--correct takes --note, and no --experiment.")
    return await correct_row(parts, args.correct, args.note)


def _plan_inputs(
    parts: BenchParts, revision: str, baseline: Path, candidate: Path
) -> tuple[LoadedSettings, LoadedSettings, GateRule, TaskSetView, Split] | str:
    # In the order of the refusals of `bench plan`.
    fault = revision_fault(revision, clean=True)
    if fault is not None:
        return fault
    base = loaded(parts, baseline)
    if isinstance(base, str):
        return base
    cand = loaded(parts, candidate)
    if isinstance(cand, str):
        return cand
    rule = parts.rule()
    if isinstance(rule, Failure):
        return rule.message
    view = task_view(parts.root)
    if isinstance(view, Failure):
        return view.message
    if view.faults or view.split is None:
        return view.faults[0]
    return base, cand, rule, view, view.split


def _names(row: LedgerRow, name: str) -> bool:
    if isinstance(row, PlanRow):
        return row.experiment == name
    if isinstance(row, RunSetRow) and not row.experiment:
        return row.body.run_set.get("run_set") == name
    return False


def _check_faults(
    task: str, oracle: Sequence[TrialFacts], nop: Sequence[TrialFacts]
) -> list[str]:
    faults: list[str] = []
    passed = sum(f.check == "passed" for f in oracle if f.task == task)
    if passed != ORACLE_ATTEMPTS:
        faults.append(
            f"{task}: the reference solution passed {passed} of"
            f" {ORACLE_ATTEMPTS} trials."
        )
    ends = [f.check for f in nop if f.task == task]
    if ends != ["failed"]:
        faults.append(
            f"{task}: doing nothing should fail once, and the check ended:"
            f" {', '.join(ends) or 'with no trial'}."
        )
    return faults


def _started(facts: TrialFacts) -> tuple[datetime, str]:
    return datetime.fromisoformat(facts.started_at), facts.runner_name
