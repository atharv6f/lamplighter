"""The real start script and Harbor for tests, and a job run in a child to be stopped.

A child runs `HarborJobs.run` in a session of its own, so that a test can
kill or interrupt the whole chain at once: the child, the start script,
`uv` and Harbor, as a kill of `bench run` or a Ctrl-C would.
"""

import asyncio
import dataclasses
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from lamplighter.adapters.harbor_jobs import HarborJobs, HarborPaths
from lamplighter.app.code_revision import REPO_ROOT, code_revision
from lamplighter.app.config import load_config
from lamplighter.app.main import tool_entries
from lamplighter.core import portable_config_id
from lamplighter.ports import HarnessAgentSpec, Job, from_plain, to_plain
from tests.container_kit import command_user
from tests.test_bench_trial import quick_settings

HARBOR = REPO_ROOT / "bench" / "harbor"
TASKS = REPO_ROOT / "tests" / "harbor_tasks"
SHARED_FOLDER = "harbor_shim.environment:SharedFolderDocker"
HARNESS_AGENT = "harbor_shim.agent:HarnessAgent"
_PROJECT = '{{.Label "com.docker.compose.project"}}'
SHELL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
BUDGET = 30_000_000


def harbor_paths(bench: Path, agent_class: str = HARNESS_AGENT) -> HarborPaths:
    """The real start script and compose file, the tests' tasks, and a bench folder."""
    return HarborPaths(
        start_script=HARBOR / "start.sh",
        bench=bench,
        tasks=TASKS,
        compose=HARBOR / "no_network.yaml",
        agent_class=agent_class,
        environment_class=SHARED_FOLDER,
    )


def harness_job(bench: Path, run_set: str, task: str, attempts: int = 1) -> Job:
    """A job of the harness on one of the tests' tasks, with its settings copy made.

    The planned ids are those of the copy and of this checkout, so a trial
    that runs this code on it counts.
    """
    settings = bench / "run_sets" / run_set / "settings.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text(quick_settings())
    config = load_config(settings)
    portable = portable_config_id(
        config.model,
        config.prices,
        config.run,
        tool_entries(config),
        dataclasses.asdict(config.tools),
    )
    agent = HarnessAgentSpec(
        kind="harness",
        settings=str(settings),
        shell_path=SHELL_PATH,
        budget_micro_dollars=BUDGET,
        portable_config_id=portable,
        code_revision=code_revision(REPO_ROOT),
        run_as=command_user(),
    )
    return Job(run_set, (task,), attempts, 2, False, agent)


def child_run(paths: HarborPaths, job: Job) -> subprocess.Popen[bytes]:
    """Start `run` of the job in a child, in a session of its own.

    The paths are those of `harbor_paths`, with their bench and agent class.
    """
    plain = {
        "bench": str(paths.bench),
        "agent_class": paths.agent_class,
        "job": to_plain(job),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "tests.harbor_kit", json.dumps(plain)],
        cwd=REPO_ROOT,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for(
    child: subprocess.Popen[bytes],
    job_folder: Path,
    ready: Callable[[list[str], list[str]], bool],
) -> None:
    """Wait while the child runs until `ready(finished, unfinished)` holds.

    Each list names trial folders: those with a result, and those without.
    """
    deadline = time.monotonic() + 600
    while True:
        assert child.poll() is None, "the job ended before it was ready"
        assert time.monotonic() < deadline
        folders = (
            sorted(p for p in job_folder.iterdir() if p.is_dir())
            if job_folder.is_dir()
            else []
        )
        finished = [p.name for p in folders if (p / "result.json").exists()]
        unfinished = [p.name for p in folders if p.name not in finished]
        if ready(finished, unfinished):
            return
        time.sleep(0.05)


def agents_up(trials: list[str]) -> bool:
    """Say whether each trial has its agent's container, and there is a trial."""
    up = projects("container")
    return bool(trials) and all(f"{trial.lower()}__env" in up for trial in trials)


def stop(child: subprocess.Popen[bytes], how: signal.Signals, lock: Path) -> None:
    """Send `how` to the child's whole session, and wait until all of it has ended.

    The child can end a moment before Harbor does, so the end is when the
    run set's lock, which the whole chain holds, is free again.
    """
    os.killpg(child.pid, how)
    child.wait(timeout=300)
    deadline = time.monotonic() + 30
    descriptor = os.open(lock, os.O_RDWR)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                assert time.monotonic() < deadline, "the chain still holds the lock"
                time.sleep(0.05)
    finally:
        os.close(descriptor)


def projects(kind: str) -> set[str]:
    """Give the compose project of every container or network Docker has."""
    words = ["container", "ls", "--all"] if kind == "container" else ["network", "ls"]
    out = subprocess.run(
        ["docker", *words, "--format", _PROJECT],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line for line in out.splitlines() if line}


def _main(argument: str) -> int:
    plain = json.loads(argument)
    paths = harbor_paths(Path(plain["bench"]), plain["agent_class"])
    job = from_plain(Job, plain["job"])
    answer = asyncio.run(HarborJobs(paths).run(job))
    return 0 if answer is None else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1]))
