"""What every part of the bench command is given, and the steps two parts share.

Each part is given its ports, the settings, the clock, the id source and
where to print, so a test runs it on the two fakes.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from lamplighter.app.bench_settings import BenchSettings, read_rule
from lamplighter.app.code_revision import UNKNOWN, code_revision
from lamplighter.app.config import Config, load_config
from lamplighter.app.task_files import ignored_task_files, task_files
from lamplighter.app.task_set_view import TaskSetView, task_set_view
from lamplighter.bench import GateRule
from lamplighter.ports import (
    BenchJobs,
    Clock,
    ExperimentLedger,
    Failure,
    IdSource,
    LedgerRow,
)

NO_KEY = "ANTHROPIC_API_KEY is not set."
AS_ROOT = (
    "The bench does not run as root: a command in a task's container would"
    " then be root too."
)
NOT_CAPPED = (
    "Claude Code keeps the key inside the container. Run it with the capped"
    " key of .env.bench."
)
NO_CHECKOUT = "The bench needs a git checkout, to record which code ran."
NOT_CLEAN = (
    "Commit your changes first: an experiment must name code that can be found again."
)
HELD_OUT = "The held-out tasks are run only by a planned experiment."
CHANGED_DURING = (
    "The checkout changed during the run set, so it is not written. Commit or"
    " undo the change, and run the command again."
)
DIRTY = "+dirty."


@dataclass(frozen=True)
class BenchParts:
    """What the bench command is built from. A test gives its own.

    `root` is the checkout, which holds `bench/`. `check_jobs` keeps its
    job folders apart from the run sets', under `checks/` in the bench
    folder. `portable_id` gives the portable config id of loaded settings.
    `environ` is where the key and the mark of a capped key are read, and
    `user` is the process's user id and group id, which a command in a
    task's container runs as.
    """

    jobs: BenchJobs
    check_jobs: BenchJobs
    ledger: ExperimentLedger
    clock: Clock
    ids: IdSource
    root: Path
    settings: BenchSettings
    harbor_version: str
    portable_id: Callable[[Config], str]
    environ: Mapping[str, str]
    user: tuple[int, int]
    out: TextIO
    errors: TextIO

    def refuse(self, message: str) -> int:
        """Print why the command stops, and give its exit code."""
        print(message, file=self.errors)
        return 1

    def revision(self) -> str:
        """Give the code revision of the checkout as it is now."""
        return code_revision(self.root)

    def rule(self) -> GateRule | Failure:
        """Read the gate's rule from the checkout."""
        return read_rule(self.root / "bench" / "gate_rule.toml")

    def run_sets(self) -> Path:
        """Give the folder that holds a folder per run set, with its settings copy."""
        return Path(self.settings.dir) / "run_sets"


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    """A harness settings file, loaded, with its portable id and the SHA-256 of it."""

    path: Path
    config: Config
    portable_id: str
    sha256: str


def task_view(root: Path) -> TaskSetView | Failure:
    """Read the checkout's task files and give what they hold, faults and all."""
    files = task_files(root)
    if isinstance(files, Failure):
        return files
    ignored = ignored_task_files(root)
    if isinstance(ignored, Failure):
        return ignored
    return task_set_view(files, ignored)


def loaded(parts: BenchParts, path: Path) -> LoadedSettings | str:
    """Load a settings file of the harness, or give the loader's line with the path."""
    try:
        data = path.read_bytes()
        config = load_config(path)
    except (OSError, ValueError) as error:
        return f"{path}: {error}"
    digest = hashlib.sha256(data).hexdigest()
    return LoadedSettings(path, config, parts.portable_id(config), digest)


def revision_fault(revision: str, clean: bool) -> str | None:
    """Give why a code revision cannot be used, or None.

    `unknown` never can. With `clean`, a changed checkout cannot either.
    """
    if revision == UNKNOWN:
        return NO_CHECKOUT
    if clean and DIRTY in revision:
        return NOT_CLEAN
    return None


async def ledger_rows(parts: BenchParts) -> list[LedgerRow] | Failure:
    """Give every row of the ledger."""
    rows = await parts.ledger.rows()
    return rows if isinstance(rows, Failure) else list(rows)
