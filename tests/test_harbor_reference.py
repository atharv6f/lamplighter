"""The reference solution in Harbor and Docker, run as a trial's commands are.

A task that only root could do passes under Harbor's own reference agent,
which runs as root, and fails under the shim's, which runs as the command
user in a seeded box.
"""

import asyncio
import dataclasses
import shutil
from pathlib import Path

import pytest

from lamplighter.adapters.harbor_jobs import HarborJobs
from lamplighter.ports import (
    AgentSpec,
    Failure,
    Job,
    ReferenceAgentSpec,
    RunnerAgentSpec,
    TrialFacts,
)
from tests.container_kit import command_user
from tests.harbor_kit import TASKS, harbor_paths

pytestmark = pytest.mark.container
AS_COMMANDS = ReferenceAgentSpec("reference", command_user())
AS_ROOT = RunnerAgentSpec("runner", "oracle", None, {})


def checked(
    tmp_path: Path, agent: AgentSpec, task: str, tasks: Path = TASKS
) -> TrialFacts:
    paths = dataclasses.replace(harbor_paths(tmp_path / "bench"), tasks=tasks)
    jobs = HarborJobs(paths)
    job = Job("made-up--reference", (task,), 1, 1, False, agent)
    assert asyncio.run(jobs.run(job)) is None
    found = asyncio.run(jobs.trials(job.run_set))
    assert not isinstance(found, Failure), found
    (one,) = found
    return one


def printed(facts: TrialFacts) -> str:
    return (Path(facts.folder) / "agent" / "reference.txt").read_text()


def test_the_reference_solution_passes_as_the_command_user(tmp_path: Path) -> None:
    facts = checked(tmp_path, AS_COMMANDS, "change-one-port")
    assert facts.check == "passed"
    assert printed(facts).startswith("exit 0\n")


def test_a_task_only_root_can_do_passes_as_root_and_fails_as_the_command_user(
    tmp_path: Path,
) -> None:
    assert checked(tmp_path / "root", AS_ROOT, "only-root-can-read").check == "passed"
    facts = checked(tmp_path / "user", AS_COMMANDS, "only-root-can-read")
    assert facts.check == "failed"
    assert "Permission denied" in printed(facts)


def test_a_solution_closed_to_others_on_this_machine_still_runs(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    shutil.copytree(TASKS / "change-one-port", tasks / "change-one-port")
    solution = tasks / "change-one-port" / "solution"
    (solution / "solve.sh").chmod(0o600)
    solution.chmod(0o700)
    assert checked(tmp_path, AS_COMMANDS, "change-one-port", tasks).check == "passed"
