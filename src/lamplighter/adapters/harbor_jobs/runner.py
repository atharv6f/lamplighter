"""The job runner of the bench, through Harbor: a job file out, a job folder in.

Harbor runs as a child, started by the pinned project's start script,
which passes it no variable of this process but those it names.
"""

import asyncio
import contextlib
import fcntl
import json
import os
import subprocess
from collections.abc import Generator, Sequence
from pathlib import Path

from lamplighter.adapters.harbor_jobs.job_file import (
    HarborPaths,
    job_file,
    without_pace,
)
from lamplighter.adapters.harbor_jobs.job_folder import CANCELLED, finished_trials
from lamplighter.adapters.harbor_jobs.leftovers import (
    cut_off_trials,
    docker_answers,
    remove_leftovers,
)
from lamplighter.ports import BenchJobs, Failure, Job, TrialFacts, canonical_json

_SOURCE = "harbor_jobs"


class HarborJobs(BenchJobs):
    """Runs a run set's job in Harbor, and reads what Harbor recorded.

    Each run set has a folder under `run_sets/` with its job file, its lock
    and Harbor's printed output, and Harbor's job folder under `jobs/`.
    The lock is held by the open file, which the child is given too, so a
    kill of this process alone leaves the run set locked while Harbor runs.
    """

    def __init__(self, paths: HarborPaths) -> None:
        """Keep where the start script, the bench folder and the tasks are."""
        self._paths = paths

    async def run(self, job: Job) -> Failure | None:
        """Run the job, or carry it on. A wait for Harbor blocks no task."""
        return await asyncio.to_thread(self._run, job)

    async def trials(self, run_set: str) -> Sequence[TrialFacts] | Failure:
        """Give the facts of each finished trial of the run set."""
        return await asyncio.to_thread(finished_trials, self._job_folder(run_set))

    def _run(self, job: Job) -> Failure | None:
        try:
            return self._locked_run(job)
        except OSError as error:
            return Failure(
                "unavailable",
                "none",
                f"The run set's files could not be used: {error}.",
                _SOURCE,
            )

    def _locked_run(self, job: Job) -> Failure | None:
        own = self._paths.bench / "run_sets" / job.run_set
        own.mkdir(parents=True, exist_ok=True)
        with _held(own / "lock") as lock:
            if lock is None:
                return _conflict("This run set is still running.")
            answer = docker_answers() or self._job_written(job, own / "job.json")
            if answer is not None:
                return answer
            folder = self._job_folder(job.run_set)
            # Before Harbor runs: a resume deletes the folders that name them.
            left = remove_leftovers(cut_off_trials(folder))
            if left is not None:
                return left
            return self._started(own, folder, lock)

    def _job_folder(self, run_set: str) -> Path:
        return self._paths.bench / "jobs" / run_set

    def _job_written(self, job: Job, path: Path) -> Failure | None:
        wanted = job_file(job, self._paths)
        if not path.exists():
            _write_whole(path, canonical_json(wanted) + "\n")
            return None
        try:
            first = json.loads(path.read_bytes())
        except ValueError:
            first = None
        if without_pace(first) != without_pace(json.loads(canonical_json(wanted))):
            return _conflict(
                f"The run set was started as another job. Its first job file is {path}."
            )
        return None

    def _started(self, own: Path, folder: Path, lock: int) -> Failure | None:
        # A job folder with no copy of the job holds no trial: Harbor was
        # stopped before it began, and a resume of it fails every time.
        if (folder / "config.json").exists():
            call = ["job", "resume", "-p", str(folder), "-f", CANCELLED]
        else:
            call = ["run", "-c", str(own / "job.json")]
        log = own / "harbor.log"
        with log.open("ab") as out:
            ended = subprocess.run(
                ["/bin/sh", str(self._paths.start_script), *call],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                pass_fds=(lock,),
                check=False,
            )
        if ended.returncode != 0:
            return Failure(
                "unavailable",
                "none",
                f"Harbor ended with exit code {ended.returncode}. What it printed"
                f" is in {log}.",
                _SOURCE,
            )
        return None


@contextlib.contextmanager
def _held(path: Path) -> Generator[int | None]:
    # Without waiting: a run set that is running is a conflict, not a queue.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        yield descriptor
    finally:
        os.close(descriptor)


def _write_whole(path: Path, text: str) -> None:
    # Through a renamed file, so that a kill never leaves half a job file,
    # which would read as another job for ever.
    temporary = path.with_name(f"{path.name}.new")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _conflict(message: str) -> Failure:
    return Failure("conflict", "none", message, _SOURCE)
