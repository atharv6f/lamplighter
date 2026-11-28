"""Finds and removes the containers and networks that cut-off trials of one job left.

Harbor names each trial's compose project after the trial, in lower case:
the trial followed by `__env` for the agent's container, and by
`__verifier__` and a key for a grading one. The job is in neither name, so
the trials are read from the job folder, before Harbor is called and
deletes the folders of the ones that were cut off. The match is on the
whole trial name, which ends in random letters, so that another run set
on the same task keeps its containers.
"""

import subprocess
from pathlib import Path

from lamplighter.ports import Failure

_SOURCE = "harbor_jobs"
_DOCKER_S = 60
_PROJECT = '{{.ID}} {{.Label "com.docker.compose.project"}}'


def cut_off_trials(job_folder: Path) -> list[str]:
    """Give the names of the trial folders with no result, in lower case."""
    if not job_folder.is_dir():
        return []
    return sorted(
        folder.name.lower()
        for folder in job_folder.iterdir()
        if folder.is_dir() and not (folder / "result.json").exists()
    )


def is_left_by(project: str, trials: list[str]) -> bool:
    """Say whether a compose project is the agent's or a check's of a trial."""
    return any(
        project == f"{trial}__env" or project.startswith(f"{trial}__verifier__")
        for trial in trials
    )


def remove_leftovers(trials: list[str]) -> Failure | None:
    """Remove the containers of the trials, then their networks."""
    if not trials:
        return None
    for listed, removed in (
        (["container", "ls", "--all"], ["container", "rm", "--force"]),
        (["network", "ls"], ["network", "rm"]),
    ):
        found = _docker([*listed, "--no-trunc", "--format", _PROJECT])
        if isinstance(found, Failure):
            return found
        left = [
            ident
            for ident, _, project in (
                line.partition(" ") for line in found.splitlines()
            )
            if is_left_by(project, trials)
        ]
        if left:
            gone = _docker([*removed, *left])
            if isinstance(gone, Failure):
                return gone
    return None


def docker_answers() -> Failure | None:
    """Say whether Docker's daemon answers."""
    found = _docker(["version", "--format", "{{.Server.Version}}"])
    return found if isinstance(found, Failure) else None


def _docker(words: list[str]) -> str | Failure:
    try:
        done = subprocess.run(
            ["docker", *words],
            capture_output=True,
            text=True,
            timeout=_DOCKER_S,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _unavailable(words, str(error))
    if done.returncode != 0:
        return _unavailable(words, done.stderr.strip()[-500:])
    return done.stdout


def _unavailable(words: list[str], said: str) -> Failure:
    return Failure(
        "unavailable",
        "none",
        f"Docker did not answer `docker {' '.join(words[:2])}`: {said or 'no output'}.",
        _SOURCE,
    )
