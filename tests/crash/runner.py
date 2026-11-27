"""Starts the crash child, kills it, and runs the checks after every kill."""

import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tests.crash.checks import check_store, read_report
from tests.group_kill import kill_group

ROOT = Path(__file__).parents[2]

# How long to wait after the child is ready before the kill, in milliseconds.
# A slow drive makes each write slower, so the Mac run can ask for more.
WAIT_MS = tuple(
    int(n) for n in os.environ.get("LAMPLIGHTER_KILL_WAIT_MS", "5,200").split(",")
)


@dataclass(frozen=True)
class KillRun:
    """What a run of random kills did."""

    kills: int
    batches: int
    kills_after_a_write: int


def start(mode: str, folder: Path, report: Path, seed: int) -> subprocess.Popen[bytes]:
    """Start the child in its own process group, so a kill reaches all of it."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.crash.child",
            mode,
            str(folder),
            str(report),
            str(seed),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def run_planted(mode: str, folder: Path) -> str:
    """Run one planted kill to its end, and give the child's report."""
    report = folder / "report.jsonl"
    child = start(mode, folder, report, 0)
    try:
        _, errors = child.communicate(timeout=60)
    finally:
        kill_group(child)
    assert child.returncode == -signal.SIGKILL, errors.decode()
    return report.read_text() if report.exists() else ""


def run_random_kills(folder: Path, kills: int, seed: int) -> KillRun:
    """Kill the child `kills` times at random moments, checking after each kill."""
    chance = random.Random(seed)
    report = folder / "report.jsonl"
    batches = 0
    for run in range(kills):
        child = start("random", folder, report, seed * 10_000 + run)
        try:
            _wait_for_ready(child, report, run + 1)
            time.sleep(chance.uniform(*WAIT_MS) / 1000)
        finally:
            kill_group(child)
        batches = check_store(folder, read_report(report))
    reported = read_report(report)
    return KillRun(
        kills=kills, batches=batches, kills_after_a_write=reported.runs_that_wrote
    )


def _wait_for_ready(child: subprocess.Popen[bytes], report: Path, runs: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if report.exists() and report.read_text().count('"ready"') >= runs:
            return
        if child.poll() is not None:
            raise AssertionError(child.stderr.read().decode() if child.stderr else "")
        time.sleep(0.002)
    raise AssertionError("the child never said it was ready")
