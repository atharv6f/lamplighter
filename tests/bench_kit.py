"""A made-up checkout with a task set, and the bench command on the two fakes.

The checkout is a git repository of its own in a temp folder, so its code
revision is real: a change to a file under `bench/` makes it changed.
"""

import asyncio
import dataclasses
import io
import shutil
import subprocess
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path

from lamplighter.adapters import CountingIds
from lamplighter.adapters.in_memory import FakeBenchJobs, InMemoryLedger
from lamplighter.app.bench_parts import BenchParts
from lamplighter.app.bench_settings import BenchSettings
from lamplighter.app.code_revision import code_revision
from lamplighter.app.main import settings_portable_id
from lamplighter.bench import GROUPS, Split, TrialRecord, record_text, split
from lamplighter.ports import (
    CheckEnd,
    Failure,
    HarnessAgentSpec,
    Job,
    LedgerRow,
    ReferenceAgentSpec,
    TrialFacts,
    canonical_json,
)
from tests.bench.test_trial_record import RECORD
from tests.fake_clock import START, FakeClock
from tests.test_bench_trial import quick_settings

TASK = Path(__file__).parent / "harbor_tasks" / "change-one-port"
RULE = """\
metric = "pass_fraction_per_task"
method = "paired_sign_flip_mid_p"
alpha_permille = 50
trials_per_task = 3
infra_limit_permille = 50

[guards]
cost_per_valid_trial_permille = 1250
"""
SEED = "made-up-seed"
# Two tasks in each of the six groups: twelve, of which ten are dev.
TASKS = tuple(f"{group}-{n}" for group in sorted(GROUPS) for n in ("one", "two"))
USER = (1001, 1001)
HARBOR_VERSION = "0.0.0-made-up"
ENVIRON = {"ANTHROPIC_API_KEY": "made-up-key"}


def git(root: Path, *args: str) -> None:
    """Run git in the made-up checkout, as a made-up author."""
    subprocess.run(
        ["git", "-c", "user.name=Made Up", "-c", "user.email=made-up@example.invalid"]
        + ["-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def made_up_checkout(root: Path) -> Path:
    """Make a committed checkout with twelve tasks, their split and the rule."""
    tasks = root / "bench" / "tasks"
    for name in TASKS:
        folder = tasks / name
        shutil.copytree(TASK, folder)
        group = name.rsplit("-", 1)[0]
        toml = (folder / "task.toml").read_text()
        toml = toml.replace("change-one-port", name).replace('"files"', f'"{group}"')
        (folder / "task.toml").write_text(toml)
    found = split(SEED, {g: [t for t in TASKS if t.startswith(g)] for g in GROUPS})
    assert isinstance(found, Split)
    dev = ", ".join(f'"{t}"' for t in found.dev)
    held_out = ", ".join(f'"{t}"' for t in found.held_out)
    (root / "bench" / "split.toml").write_text(
        f'seed = "{SEED}"\ndev = [{dev}]\nheld_out = [{held_out}]\n'
    )
    (root / "bench" / "gate_rule.toml").write_text(RULE)
    (root / "src").mkdir()
    (root / "src" / "code.py").write_text("# Made-up code.\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Made up")
    return root


def made_up_trials(
    folders: Path,
    job: Job,
    check: Callable[[str, int], CheckEnd],
    record: Callable[[str, int], TrialRecord | None],
) -> list[TrialFacts]:
    """Give one finished trial per task and attempt, its check and record by `check`."""
    found: list[TrialFacts] = []
    for task in job.tasks:
        for attempt in range(job.attempts):
            made = record(task, attempt)
            found.append(
                TrialFacts(
                    task=task,
                    runner_name=f"{task}__{attempt}",
                    started_at=(START + timedelta(seconds=attempt)).isoformat(),
                    ms=60_000,
                    check=check(task, attempt),
                    stopped_by="",
                    task_folder_hash="h" * 64,
                    harness_version="made-up-version",
                    tokens=None,
                    micro_dollars=None,
                    record=None if made is None else record_text(made),
                    folder=str(folders / job.run_set / f"{task}__{attempt}"),
                )
            )
    return found


def harness_record(job: Job) -> TrialRecord:
    """A gradable record on the job's planned id and code."""
    agent = job.agent
    assert isinstance(agent, HarnessAgentSpec)
    return dataclasses.replace(
        RECORD,
        portable_config_id=agent.portable_config_id,
        code_revision=agent.code_revision,
    )


class WatchedJobs(FakeBenchJobs):
    """The fake job runner, noting the ledger's rows each time a job starts.

    `when_run` is called with each job before it runs, so a test can change
    the checkout while a run set runs.
    """

    def __init__(
        self, trials_of: Callable[[Job], Sequence[TrialFacts]], ledger: InMemoryLedger
    ) -> None:
        super().__init__(trials_of)
        self.ledger = ledger
        self.rows_at_run: list[list[LedgerRow]] = []
        self.when_run: Callable[[Job], None] = lambda job: None

    async def run(self, job: Job) -> Failure | None:
        rows = await self.ledger.rows()
        assert not isinstance(rows, Failure)
        self.rows_at_run.append(list(rows))
        self.when_run(job)
        return await super().run(job)


class Bench:
    """The bench command on a made-up checkout, the two fakes and a fake clock.

    `passes(job, task, attempt)` decides each check of a harness job; it
    passes every check unless a test sets another. `checked` decides each
    check of the reference solution and of doing nothing.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = made_up_checkout(tmp_path / "checkout")
        self.folder = tmp_path / "bench"
        # Where the made-up trials' folders are said to be. None is made.
        self.trial_folders = tmp_path / "trials"
        self.baseline = tmp_path / "baseline.toml"
        self.baseline.write_text(quick_settings())
        self.candidate = tmp_path / "candidate.toml"
        self.candidate.write_text(quick_settings(max_steps="1"))
        self.passes: Callable[[Job, str, int], bool] = lambda job, task, n: True
        self.checked: Callable[[Job, str, int], CheckEnd] = _right_check
        self.record: Callable[[Job, str, int], TrialRecord | None] = _harness_only
        self.ledger = InMemoryLedger()
        self.jobs = WatchedJobs(self._trials, self.ledger)
        self.check_jobs = WatchedJobs(self._checks, self.ledger)
        self.clock = FakeClock()
        self.ids = CountingIds("row")
        self.environ = dict(ENVIRON)
        self.user: tuple[int, int] = USER
        self.out = io.StringIO()
        self.errors = io.StringIO()

    def parts(self) -> BenchParts:
        settings = BenchSettings(str(self.folder), "/usr/bin:/bin", 30_000_000, 4)
        return BenchParts(
            jobs=self.jobs,
            check_jobs=self.check_jobs,
            ledger=self.ledger,
            clock=self.clock,
            ids=self.ids,
            root=self.root,
            settings=settings,
            harbor_version=HARBOR_VERSION,
            portable_id=settings_portable_id,
            environ=self.environ,
            user=self.user,
            out=self.out,
            errors=self.errors,
        )

    def rows(self) -> list[LedgerRow]:
        found = asyncio.run(self.ledger.rows())
        assert not isinstance(found, Failure)
        return list(found)

    def kinds(self) -> list[tuple[str, str, str]]:
        """Each row's kind, experiment and side, in order."""
        return [(r.kind, r.experiment, _side(r)) for r in self.rows()]

    def revision(self) -> str:
        return code_revision(self.root)

    def change(self, path: str = "bench/new.txt") -> None:
        """Change the checkout, so that its code revision is marked changed."""
        (self.root / path).write_text("A made-up change.\n")

    def said(self) -> str:
        return self.errors.getvalue()

    def _trials(self, job: Job) -> list[TrialFacts]:
        def check(task: str, attempt: int) -> CheckEnd:
            return "passed" if self.passes(job, task, attempt) else "failed"

        return made_up_trials(
            self.trial_folders, job, check, lambda t, n: self.record(job, t, n)
        )

    def _checks(self, job: Job) -> list[TrialFacts]:
        return made_up_trials(
            self.trial_folders,
            job,
            lambda t, n: self.checked(job, t, n),
            lambda t, n: None,
        )


def _harness_only(job: Job, task: str, attempt: int) -> TrialRecord | None:
    # A baseline harness writes no record.
    return harness_record(job) if isinstance(job.agent, HarnessAgentSpec) else None


def _right_check(job: Job, task: str, attempt: int) -> CheckEnd:
    return "passed" if isinstance(job.agent, ReferenceAgentSpec) else "failed"


def _side(row: LedgerRow) -> str:
    body = row.body
    side = getattr(body, "side", "")
    return side if isinstance(side, str) else ""


def row_texts(rows: Sequence[LedgerRow]) -> list[str]:
    """Each row as its line, for a comparison of two ledgers."""
    return [canonical_json(row) for row in rows]
