"""`bench run` on the two fakes: an experiment to its decision, and a loose run set."""

import asyncio
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest

from lamplighter.app.bench_commands import plan_experiment
from lamplighter.app.bench_parts import (
    AS_ROOT,
    CHANGED_DURING,
    HELD_OUT,
    NO_CHECKOUT,
    NO_KEY,
    NOT_CAPPED,
    NOT_CLEAN,
)
from lamplighter.app.bench_run import STALE_PLAN, run_experiment, run_loose
from lamplighter.bench import HARNESS, GateDecision, RunSet, Tokens
from lamplighter.ports import (
    DecisionRow,
    Failure,
    HarnessAgentSpec,
    Job,
    LedgerRow,
    PlanRow,
    RunnerAgentSpec,
    RunSetRow,
    SplitName,
    TrialFacts,
    from_plain,
)
from tests.bench_kit import USER, Bench, git, harness_record

NAME = "made-up"
WHOLE = [
    ("plan", NAME, ""),
    ("start", NAME, "baseline"),
    ("run_set", NAME, "baseline"),
    ("start", NAME, "candidate"),
    ("run_set", NAME, "candidate"),
    ("decision", NAME, ""),
]


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


def _plan(bench: Bench, split: SplitName = "held_out", same: bool = False) -> int:
    candidate = bench.baseline if same else bench.candidate
    return asyncio.run(
        plan_experiment(bench.parts(), NAME, bench.baseline, candidate, split, "")
    )


def _experiment(bench: Bench) -> int:
    return asyncio.run(run_experiment(bench.parts(), NAME))


def _loose(
    bench: Bench, name: str = "made-up-loose", harness: str = HARNESS, *tasks: str
) -> int:
    parts = bench.parts()
    return asyncio.run(run_loose(parts, name, harness, tasks, bench.baseline, ""))


def _decision(bench: Bench) -> GateDecision:
    row = next(r for r in bench.rows() if isinstance(r, DecisionRow))
    return from_plain(GateDecision, row.body.decision)


def _run_set_rows(bench: Bench) -> list[RunSetRow]:
    return [r for r in bench.rows() if isinstance(r, RunSetRow)]


# An experiment run to its end.


def test_an_experiment_writes_two_starts_two_run_sets_and_a_decision(
    bench: Bench,
) -> None:
    assert _plan(bench) == 0
    assert _experiment(bench) == 0
    assert bench.kinds() == WHOLE
    decision = next(r for r in bench.rows() if isinstance(r, DecisionRow))
    assert (decision.body.held_out_uses, decision.body.same_config) == (1, False)
    assert "The gate's answer: inconclusive" in bench.out.getvalue()
    assert "Noise floor: no A/A experiment" in bench.out.getvalue()


def test_each_sides_start_is_in_the_ledger_before_its_job_runs(bench: Bench) -> None:
    _plan(bench)
    _experiment(bench)
    seen = [
        [(r.kind, getattr(r.body, "side", "")) for r in rows]
        for rows in bench.jobs.rows_at_run
    ]
    assert seen[0][-1] == ("start", "baseline")
    assert seen[1][-1] == ("start", "candidate")
    assert [job.run_set for job in bench.jobs.jobs] == [
        f"{NAME}--baseline",
        f"{NAME}--candidate",
    ]


def test_each_side_runs_its_planned_settings_copy_on_the_planned_ids(
    bench: Bench,
) -> None:
    _plan(bench)
    _experiment(bench)
    plan = bench.rows()[0]
    for job, settings, planned in zip(
        bench.jobs.jobs, (bench.baseline, bench.candidate), plan.config_ids, strict=True
    ):
        agent = job.agent
        assert isinstance(agent, HarnessAgentSpec)
        copy = bench.folder / "run_sets" / job.run_set / "settings.toml"
        assert agent.settings == str(copy)
        assert copy.read_bytes() == settings.read_bytes()
        assert (agent.portable_config_id, agent.code_revision) == (
            planned,
            bench.revision(),
        )
        assert (agent.run_as, agent.shell_path, agent.budget_micro_dollars) == (
            USER,
            "/usr/bin:/bin",
            30_000_000,
        )
        assert (job.attempts, job.concurrent_trials, job.network) == (3, 4, False)


def test_the_run_set_rows_hold_the_whole_run_sets(bench: Bench) -> None:
    _plan(bench, "dev")
    _experiment(bench)
    baseline, candidate = _run_set_rows(bench)
    found = from_plain(RunSet, baseline.body.run_set)
    assert (found.run_set, found.split, found.trials_per_task) == (
        f"{NAME}--baseline",
        "dev",
        3,
    )
    assert len(found.trials) == 30
    assert baseline.body.totals["pass"] == 30
    assert baseline.body.totals["micro_dollars"] == 30 * 1_234
    assert (baseline.body.started_at, baseline.body.concurrent_trials) == (
        "2026-11-26T07:35:12.345Z",
        4,
    )
    assert candidate.config_ids == (bench.rows()[0].config_ids[1],)


def test_a_candidate_that_passes_where_the_baseline_fails_is_kept(
    bench: Bench,
) -> None:
    bench.passes = lambda job, task, n: job.run_set.endswith("--candidate")
    _plan(bench, "dev")
    assert _experiment(bench) == 0
    assert (_decision(bench).outcome, _decision(bench).why) == ("keep", "better")


def test_a_candidate_that_fails_where_the_baseline_passes_is_rejected(
    bench: Bench,
) -> None:
    bench.passes = lambda job, task, n: job.run_set.endswith("--baseline")
    _plan(bench, "dev")
    assert _experiment(bench) == 0
    assert (_decision(bench).outcome, _decision(bench).why) == ("reject", "worse")


def test_a_run_set_at_its_money_stop_is_inconclusive_and_says_so(
    bench: Bench,
) -> None:
    def record(job: Job, task: str, attempt: int):  # noqa: ANN202 - the kit's type
        made = harness_record(job)
        if job.run_set.endswith("--candidate") and attempt == 2:
            return dataclasses.replace(
                made, gradable=False, infra_reason="budget_reached"
            )
        return made

    bench.record = record
    _plan(bench, "dev")
    assert _experiment(bench) == 0
    assert _decision(bench).why == "budget_reached"
    assert "reached its money stop" in bench.out.getvalue()


def test_an_a_a_experiment_is_the_noise_floor_shown_beside_its_decision(
    bench: Bench,
) -> None:
    _plan(bench, "dev", same=True)
    assert _experiment(bench) == 0
    decision = next(r for r in bench.rows() if isinstance(r, DecisionRow))
    assert decision.body.same_config
    assert (
        "Noise floor, from the A/A experiment made-up: a difference of 0.00 points,"
        " a cost ratio of 1.000." in bench.out.getvalue()
    )


def test_a_finished_experiment_run_again_writes_nothing_more(bench: Bench) -> None:
    _plan(bench)
    _experiment(bench)
    before = bench.rows()
    assert _experiment(bench) == 0
    assert bench.rows() == before
    assert len(bench.jobs.jobs) == 2


# Cut off and carried on.


class _Stop(Exception):
    """Plays a kill of the command at one point."""


class _Points:
    def __init__(self, at: int) -> None:
        self.at = at
        self.passed = 0

    def tick(self) -> None:
        self.passed += 1
        if self.passed == self.at:
            raise _Stop


class _StoppingLedger:
    def __init__(self, bench: Bench, points: _Points) -> None:
        self._inner = bench.ledger
        self._points = points

    async def append(self, row: LedgerRow) -> Failure | None:
        self._points.tick()
        answer = await self._inner.append(row)
        self._points.tick()
        return answer

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        self._points.tick()
        return await self._inner.rows()


class _StoppingJobs:
    def __init__(self, bench: Bench, points: _Points) -> None:
        self._inner = bench.jobs
        self._points = points

    async def run(self, job: Job) -> Failure | None:
        self._points.tick()
        answer = await self._inner.run(job)
        self._points.tick()
        return answer

    async def trials(self, run_set: str) -> Sequence[TrialFacts] | Failure:
        self._points.tick()
        return await self._inner.trials(run_set)


def test_an_experiment_cut_off_at_any_point_and_run_again_writes_each_row_once(
    tmp_path: Path,
) -> None:
    whole = Bench(tmp_path / "whole")
    _plan(whole)
    _experiment(whole)
    points_tried = 0
    for at in range(1, 100):
        bench = Bench(tmp_path / str(at))
        _plan(bench)
        points = _Points(at)
        parts = dataclasses.replace(
            bench.parts(),
            ledger=_StoppingLedger(bench, points),
            jobs=_StoppingJobs(bench, points),
        )
        try:
            asyncio.run(run_experiment(parts, NAME))
        except _Stop:
            points_tried += 1
        else:
            break
        assert _experiment(bench) == 0, bench.said()
        assert bench.kinds() == WHOLE
        assert _decision(bench) == _decision(whole)
    # Every call to a port, before it and after it, was a point it was cut
    # at: a read of the ledger, then for each side a read, a start written,
    # a job run, its trials read and its run set written, then a read and a
    # decision written.
    assert points_tried == 1 + 2 * (1 + 2 + 2 + 1 + 2) + 1 + 2


def test_a_job_that_was_cut_off_is_carried_on_and_then_written(bench: Bench) -> None:
    _plan(bench)
    bench.jobs.hold_back = 2
    assert _experiment(bench) == 1
    assert "is not whole" in bench.said()
    assert f"{NAME}--baseline" in bench.said()
    assert [k for k, _, _ in bench.kinds()] == ["plan", "start"]
    assert _experiment(bench) == 0
    assert bench.kinds() == WHOLE


def test_a_job_runner_that_fails_stops_the_command_and_writes_no_run_set(
    bench: Bench,
) -> None:
    _plan(bench)
    bench.jobs.failure = Failure(
        "unavailable", "none", "Docker is not running. Made up.", "fake"
    )
    assert _experiment(bench) == 1
    assert bench.said() == "Docker is not running. Made up.\n"
    assert [k for k, _, _ in bench.kinds()] == ["plan", "start"]


def test_harbors_exit_code_is_said_with_its_log(bench: Bench) -> None:
    _plan(bench)
    line = "Harbor ended with exit code 2. See /made/up/harbor.log."
    bench.jobs.failure = Failure("unavailable", "none", line, "fake")
    assert _experiment(bench) == 1
    assert bench.said() == line + "\n"


def test_a_checkout_that_changes_during_a_run_set_is_not_written(bench: Bench) -> None:
    _plan(bench)
    bench.jobs.when_run = lambda job: bench.change()
    assert _experiment(bench) == 1
    assert bench.said() == CHANGED_DURING + "\n"
    assert [k for k, _, _ in bench.kinds()] == ["plan", "start"]


def test_a_loose_run_set_whose_checkout_changes_is_not_written(bench: Bench) -> None:
    bench.jobs.when_run = lambda job: bench.change("src/more.py")
    assert _loose(bench) == 1
    assert bench.said() == CHANGED_DURING + "\n"
    assert bench.rows() == []


def test_settings_changed_since_the_plan_are_refused_before_the_start(
    bench: Bench,
) -> None:
    _plan(bench)
    bench.baseline.write_text(bench.baseline.read_text() + "\n# A made-up change.\n")
    assert _experiment(bench) == 1
    assert "are not those the plan names. Plan a new experiment." in bench.said()
    assert [k for k, _, _ in bench.kinds()] == ["plan"]
    assert bench.jobs.jobs == []


# The refusals of `bench run`.


def _no_key(bench: Bench) -> None:
    bench.environ.clear()


def _as_root(bench: Bench) -> None:
    bench.user = (0, 0)


def _no_checkout(bench: Bench) -> None:
    bench.root = bench.root / "src"


@pytest.mark.parametrize(
    ("change", "line"),
    [
        (_no_key, NO_KEY),
        (_as_root, AS_ROOT),
        (_no_checkout, NO_CHECKOUT),
        (Bench.change, NOT_CLEAN),
    ],
)
def test_an_experiment_is_refused_before_anything_is_read(
    bench: Bench, change: object, line: str
) -> None:
    _plan(bench)
    assert callable(change)
    change(bench)
    assert _experiment(bench) == 1
    assert bench.said() == line + "\n"
    assert [k for k, _, _ in bench.kinds()] == ["plan"]
    assert bench.jobs.jobs == []


def test_an_experiment_with_no_plan_is_refused(bench: Bench) -> None:
    assert _experiment(bench) == 1
    assert (
        bench.said() == f'No plan named {NAME}. Run "lamplighter bench plan" first.\n'
    )


@pytest.mark.parametrize(
    "path",
    ["bench/gate_rule.toml", "bench/tasks/code-one/instruction.md", "src/code.py"],
)
def test_a_plan_made_for_other_tasks_code_or_rule_is_refused(
    bench: Bench, path: str
) -> None:
    _plan(bench)
    target = bench.root / path
    target.write_text(target.read_text() + "\n# A made-up change.\n")
    git(bench.root, "commit", "-qam", "Made-up change")
    assert _experiment(bench) == 1
    assert bench.said() == STALE_PLAN + "\n"
    assert bench.jobs.jobs == []


@pytest.mark.parametrize(
    ("change", "line"),
    [(_no_key, NO_KEY), (_as_root, AS_ROOT), (_no_checkout, NO_CHECKOUT)],
)
def test_a_loose_run_set_is_refused_before_anything_is_read(
    bench: Bench, change: object, line: str
) -> None:
    assert callable(change)
    change(bench)
    assert _loose(bench) == 1
    assert bench.said() == line + "\n"
    assert bench.rows() == [] and bench.jobs.jobs == []


def test_claude_code_needs_the_capped_key(bench: Bench) -> None:
    assert _loose(bench, "made-up-loose", "claude-code") == 1
    assert bench.said() == NOT_CAPPED + "\n"
    bench.environ["LAMPLIGHTER_BENCH_KEY_IS_CAPPED"] = "yes"
    assert _loose(bench, "made-up-loose", "claude-code") == 0


def test_a_loose_run_set_name_that_is_taken_is_refused(bench: Bench) -> None:
    assert _loose(bench) == 0
    assert _loose(bench) == 1
    assert bench.said() == "A run set named made-up-loose exists.\n"
    assert len(bench.jobs.jobs) == 1


def test_a_held_out_task_is_refused_outside_an_experiment(bench: Bench) -> None:
    _plan(bench)
    plan = bench.rows()[0]
    assert isinstance(plan, PlanRow)
    task = plan.body.tasks[0]
    assert _loose(bench, "made-up-loose", HARNESS, task) == 1
    assert bench.said() == HELD_OUT + "\n"
    assert bench.jobs.jobs == []


def test_a_task_that_is_not_there_is_refused(bench: Bench) -> None:
    assert _loose(bench, "made-up-loose", HARNESS, "no-such-task") == 1
    assert bench.said() == "There is no task named no-such-task.\n"


# A loose run set.


def test_a_loose_run_set_runs_the_dev_set_and_writes_one_row(bench: Bench) -> None:
    assert _loose(bench) == 0
    (row,) = bench.rows()
    assert isinstance(row, RunSetRow)
    assert (row.experiment, row.body.side) == ("", "")
    found = from_plain(RunSet, row.body.run_set)
    assert (found.split, len(found.trials)) == ("dev", 30)
    (job,) = bench.jobs.jobs
    agent = job.agent
    assert isinstance(agent, HarnessAgentSpec)
    assert row.config_ids == (agent.portable_config_id,)
    assert job.network is False and len(job.tasks) == 10


def test_a_loose_run_set_on_a_changed_checkout_carries_the_mark(bench: Bench) -> None:
    bench.change()
    assert _loose(bench) == 0
    assert "+dirty." in _run_set_rows(bench)[0].code_revision


def test_chosen_tasks_are_run_and_no_row_is_written(bench: Bench) -> None:
    dev = from_plain(RunSet, _whole_loose(bench).body.run_set).trials[0].task
    assert _loose(bench, "made-up-chosen", HARNESS, dev) == 0
    assert bench.jobs.jobs[-1].tasks == (dev,)
    assert len(bench.rows()) == 1
    assert "no row is written" in bench.out.getvalue()


def _whole_loose(bench: Bench) -> RunSetRow:
    assert _loose(bench) == 0
    return _run_set_rows(bench)[0]


def test_a_loose_run_set_started_with_other_settings_is_refused(bench: Bench) -> None:
    bench.jobs.hold_back = 1
    assert _loose(bench) == 1
    bench.baseline.write_text(bench.baseline.read_text() + "\n# A made-up change.\n")
    assert _loose(bench) == 1
    assert "was started with other settings" in bench.said()
    assert len(bench.jobs.jobs) == 1


def test_a_baseline_harness_runs_with_a_network_and_names_no_config(
    bench: Bench,
) -> None:
    bench.record = lambda job, task, n: None
    assert _loose(bench, "made-up-loose", "terminus-2") == 0
    (job,) = bench.jobs.jobs
    assert job.network is True
    assert job.agent == RunnerAgentSpec(
        "runner", "terminus-2", "claude-opus-5-5", {"max_turns": 40}
    )
    (row,) = _run_set_rows(bench)
    assert row.config_ids == ()
    found = from_plain(RunSet, row.body.run_set)
    assert (found.harness, found.portable_config_id) == ("terminus-2", "")
    assert found.trials[0].tokens == Tokens(0, 0, 0, 0)


class _ChangingLedger:
    """Changes the checkout when the ledger is read, before any job is started."""

    def __init__(self, bench: Bench) -> None:
        self._bench = bench

    async def append(self, row: LedgerRow) -> Failure | None:
        return await self._bench.ledger.append(row)

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        self._bench.change()
        return await self._bench.ledger.rows()


def test_a_checkout_that_changed_before_the_job_starts_no_job(bench: Bench) -> None:
    parts = dataclasses.replace(bench.parts(), ledger=_ChangingLedger(bench))
    loose = run_loose(parts, "made-up-loose", HARNESS, (), bench.baseline, "")
    assert asyncio.run(loose) == 1
    assert bench.said() == CHANGED_DURING + "\n"
    assert bench.jobs.jobs == []
