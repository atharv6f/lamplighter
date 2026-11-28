"""A job runner that answers from a list a test gives it, and runs nothing."""

import dataclasses
from collections.abc import Callable, Sequence

from lamplighter.ports import BenchJobs, Failure, HarnessAgentSpec, Job, TrialFacts


class FakeBenchJobs(BenchJobs):
    """Gives each job the trials that a function of the job names.

    `jobs` is every job `run` was given, in order. A test sets `hold_back`
    to leave the last trials of the next `run` unfinished, as a job that
    was cut off or interrupted, and sets `failure` to make every `run`
    give it and run nothing.
    """

    def __init__(self, trials_of: Callable[[Job], Sequence[TrialFacts]]) -> None:
        """Keep the function that names the trials of a job."""
        self.jobs: list[Job] = []
        self.hold_back = 0
        self.failure: Failure | None = None
        self._trials_of = trials_of
        self._first: dict[str, Job] = {}
        self._done: dict[str, list[TrialFacts]] = {}

    async def run(self, job: Job) -> Failure | None:
        """Finish the trials of the job that are not finished yet."""
        self.jobs.append(job)
        if self.failure is not None:
            return self.failure
        first = self._first.setdefault(job.run_set, job)
        if _paceless(first) != _paceless(job):
            return Failure(
                "conflict",
                "none",
                "The run set was started as another job.",
                "fake_bench_jobs",
            )
        done = self._done.setdefault(job.run_set, [])
        finished = {trial.runner_name for trial in done}
        # A job that is carried on keeps the pace it was started with.
        todo = [t for t in self._trials_of(first) if t.runner_name not in finished]
        done.extend(todo[: max(len(todo) - self.hold_back, 0)])
        self.hold_back = 0
        return None

    async def trials(self, run_set: str) -> Sequence[TrialFacts] | Failure:
        """Give the finished trials of the run set."""
        return tuple(self._done.get(run_set, ()))


def _paceless(job: Job) -> Job:
    agent = job.agent
    if isinstance(agent, HarnessAgentSpec):
        agent = dataclasses.replace(agent, budget_micro_dollars=0)
    return dataclasses.replace(job, concurrent_trials=0, agent=agent)
