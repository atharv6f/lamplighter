"""The promises of the job runner, checked on the fake and, with Docker, on Harbor.

The Harbor kind runs the tests' made-up task with Harbor's reference
solution, which calls no model. Its cut-off and interrupted jobs are run in
a child that the test kills or interrupts as a whole.
"""

import asyncio
import dataclasses
import signal
import subprocess
from collections.abc import Callable, Coroutine, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import pytest

from lamplighter.adapters.harbor_jobs import HarborJobs
from lamplighter.adapters.in_memory import FakeBenchJobs
from lamplighter.ports import BenchJobs, Failure, Job, RunnerAgentSpec, TrialFacts
from tests.container_kit import IMAGE
from tests.harbor_kit import (
    agents_up,
    child_run,
    harbor_paths,
    projects,
    stop,
    wait_for,
)

TASK = "change-one-port"
ORACLE = RunnerAgentSpec(kind="runner", name="oracle", model=None, options={})


def made_up_job(attempts: int = 2) -> Job:
    return Job(
        run_set="made-up--oracle",
        tasks=(TASK,),
        attempts=attempts,
        concurrent_trials=2,
        network=False,
        agent=ORACLE,
    )


class Kind(Protocol):
    jobs: BenchJobs

    def cut_off(self, job: Job) -> None:
        """Run the job and kill it with some of its trials unfinished."""
        ...

    def interrupted(self, job: Job) -> None:
        """Run the job and interrupt it with some of its trials running."""
        ...

    def broken(self) -> Job:
        """Give a job whose run ends badly."""
        ...


class FakeKind:
    def __init__(self) -> None:
        self.fake = FakeBenchJobs(self._trials_of)
        self.jobs: BenchJobs = self.fake

    @staticmethod
    def _trials_of(job: Job) -> Sequence[TrialFacts]:
        return [
            TrialFacts(
                task=task,
                runner_name=f"{task}__{number}",
                started_at=f"2026-11-28T10:00:0{number}+00:00",
                ms=1,
                check="passed",
                stopped_by="",
                task_folder_hash="a" * 64,
                harness_version="1.0.0",
                tokens=None,
                micro_dollars=None,
                record=None,
                folder=f"/made-up/{job.run_set}/{task}__{number}",
            )
            for task in job.tasks
            for number in range(job.attempts)
        ]

    def cut_off(self, job: Job) -> None:
        self.fake.hold_back = 2
        assert run(self.jobs.run(job)) is None

    def interrupted(self, job: Job) -> None:
        self.cut_off(job)

    def broken(self) -> Job:
        self.fake.failure = Failure("unavailable", "none", "Made up.", "made-up")
        return made_up_job()


class HarborKind:
    def __init__(self, bench: Path) -> None:
        self.paths = harbor_paths(bench)
        self.jobs: BenchJobs = HarborJobs(self.paths)
        self.job_folder = bench / "jobs" / made_up_job().run_set
        self.lock = bench / "run_sets" / made_up_job().run_set / "lock"

    def cut_off(self, job: Job) -> None:
        child = child_run(self.paths, job)
        # Killed with two trials done and two running, whose containers
        # are then left behind.
        wait_for(
            child,
            self.job_folder,
            lambda done, running: len(done) >= 2 and agents_up(running),
        )
        stop(child, signal.SIGKILL, self.lock)

    def interrupted(self, job: Job) -> None:
        child = child_run(self.paths, job)
        wait_for(
            child,
            self.job_folder,
            lambda _, running: agents_up(running),
        )
        stop(child, signal.SIGINT, self.lock)

    def broken(self) -> Job:
        made_up = RunnerAgentSpec(
            kind="runner", name="made-up-agent-there-is-not", model=None, options={}
        )
        return dataclasses.replace(made_up_job(), agent=made_up)


KINDS: dict[str, Callable[[Path], Kind]] = {
    "fake": lambda _: FakeKind(),
    "harbor": HarborKind,
}


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


@pytest.fixture(
    params=[
        pytest.param("fake"),
        pytest.param("harbor", marks=pytest.mark.container),
    ]
)
def kind(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Kind]:
    yield KINDS[request.param](tmp_path / "bench")


def trials(kind: Kind, job: Job) -> Sequence[TrialFacts]:
    found = run(kind.jobs.trials(job.run_set))
    assert not isinstance(found, Failure), found
    return found


def names(found: Sequence[TrialFacts]) -> set[str]:
    return {t.runner_name for t in found}


def test_a_run_set_never_started_has_no_trials(kind: Kind) -> None:
    assert list(trials(kind, made_up_job())) == []


def test_a_finished_trial_is_never_run_again(kind: Kind) -> None:
    job = made_up_job()
    assert run(kind.jobs.run(job)) is None
    first = trials(kind, job)
    assert len(first) == 2
    assert {t.check for t in first} == {"passed"}
    assert run(kind.jobs.run(job)) is None
    assert trials(kind, job) == first


def test_a_cut_off_job_is_carried_on_and_its_finished_trials_kept(kind: Kind) -> None:
    job = made_up_job(attempts=4)
    kind.cut_off(job)
    before = trials(kind, job)
    assert 2 <= len(before) < 4
    assert run(kind.jobs.run(job)) is None
    after = trials(kind, job)
    assert len(after) == 4
    assert names(before) <= names(after)
    assert [t for t in after if t.runner_name in names(before)] == list(before)


def test_an_interrupted_job_gives_no_cancelled_trial_and_runs_it_again(
    kind: Kind,
) -> None:
    job = made_up_job(attempts=4)
    kind.interrupted(job)
    before = trials(kind, job)
    assert len(before) < 4
    assert all(t.check == "passed" for t in before)
    assert run(kind.jobs.run(job)) is None
    assert len(trials(kind, job)) == 4


def test_another_job_on_a_run_set_is_a_conflict(kind: Kind) -> None:
    job = made_up_job()
    assert run(kind.jobs.run(job)) is None
    first = trials(kind, job)
    for other in (
        dataclasses.replace(job, attempts=3),
        dataclasses.replace(job, tasks=("count-the-lines",)),
        dataclasses.replace(job, network=True),
    ):
        answer = run(kind.jobs.run(other))
        assert isinstance(answer, Failure), other
        assert (answer.kind, answer.effect) == ("conflict", "none")
    assert trials(kind, job) == first


def test_another_pace_is_the_same_job(kind: Kind) -> None:
    job = made_up_job()
    assert run(kind.jobs.run(job)) is None
    assert run(kind.jobs.run(dataclasses.replace(job, concurrent_trials=1))) is None
    assert len(trials(kind, job)) == 2


def test_a_runner_that_ends_badly_is_unavailable(kind: Kind) -> None:
    answer = run(kind.jobs.run(kind.broken()))
    assert isinstance(answer, Failure)
    assert (answer.kind, answer.effect) == ("unavailable", "none")


# Only Harbor can be still running, or leave containers behind.


@pytest.fixture
def harbor(tmp_path: Path) -> HarborKind:
    return HarborKind(tmp_path / "bench")


@pytest.mark.container
def test_a_kill_leaves_containers_that_the_next_run_removes_and_no_others(
    harbor: HarborKind,
) -> None:
    beside = "change-one-port__othrjob__env"
    # Containers another run left before this test are not the kill's.
    before = projects("container") | projects("network")
    other = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--network",
            "none",
            "--label",
            f"com.docker.compose.project={beside}",
            IMAGE,
            "sleep",
            "600",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    try:
        job = made_up_job(attempts=4)
        harbor.cut_off(job)
        left = {
            p
            for p in projects("container") | projects("network")
            if p.startswith(f"{TASK}__") and p != beside and p not in before
        }
        assert left, "the kill left nothing to remove"
        assert run(harbor.jobs.run(job)) is None
        assert not left & (projects("container") | projects("network"))
        assert beside in projects("container")
    finally:
        subprocess.run(["docker", "rm", "-f", other], capture_output=True, check=False)


@pytest.mark.container
def test_a_run_while_the_job_is_running_is_a_conflict(harbor: HarborKind) -> None:
    job = made_up_job(attempts=2)
    child = child_run(harbor.paths, job)
    try:
        wait_for(child, harbor.job_folder, lambda _, running: bool(running))
        answer = run(harbor.jobs.run(job))
        assert isinstance(answer, Failure)
        assert (answer.kind, answer.message) == (
            "conflict",
            "This run set is still running.",
        )
    finally:
        child.wait(timeout=600)
    assert run(harbor.jobs.run(job)) is None
