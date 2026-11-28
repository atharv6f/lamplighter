"""Names the code that runs: the git commit, with a mark when files were changed."""

import hashlib
import os
from pathlib import Path

from lamplighter.app.git_output import git_output
from lamplighter.ports import canonical_json

UNKNOWN = "unknown"
# The checkout's root: two folders above the lamplighter package.
REPO_ROOT = Path(__file__).resolve().parents[3]
# The paths whose changes are code. Others, such as the README, are not.
_CODE_PATHS = ("src", "bench", "pyproject.toml", "uv.lock")
_MARK_CHARS = 12


def code_revision(root: Path) -> str:
    """Give the commit of the checkout at `root`, a mark for its changes, or `unknown`.

    A clean checkout gives the commit's 40 hex characters. One with a
    changed, staged or new file under the code paths gives the commit,
    `+dirty.` and 12 hex characters that are the same for the same changes.
    It is `unknown` when `root` is not the top of a git checkout, when git
    is not there, or when git fails. Reading the state never writes to the
    checkout.
    """
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or os.path.realpath(top.strip()) != os.path.realpath(root):
        # The code then sits inside some other repository, whose commit
        # says nothing about it.
        return UNKNOWN
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--no-renames",
        "--untracked-files=all",
        "--",
        *_CODE_PATHS,
    )
    if commit is None or status is None:
        return UNKNOWN
    changed = _changed_paths(status)
    head = commit.strip()
    if not changed:
        return head
    return f"{head}+dirty.{changes_mark(root, changed)}"


def changes_mark(root: Path, paths: list[str]) -> str:
    """Give 12 hex characters that name what the changed files hold now.

    Each path is entered with the SHA-256 of its bytes, or `deleted`, or
    `folder` for a new folder that is a repository of its own, which git
    lists as one name. A link is entered by the text it points to.
    """
    entries = [[path, _state(root / path)] for path in sorted(paths)]
    digest = hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest()
    return digest[:_MARK_CHARS]


def _state(path: Path) -> str:
    if path.is_symlink():
        return hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
    if path.is_dir():
        return "folder"
    if not path.exists():
        return "deleted"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _changed_paths(status: str) -> list[str]:
    # Each entry is two status letters, a space and one path, and ends in a
    # zero byte. With renames off no entry has a second path.
    entries = [entry for entry in status.split("\0") if entry]
    return [entry[3:].rstrip("/") for entry in entries]


def _git(root: Path, *args: str) -> str | None:
    found = git_output(root, *args)
    if found is None:
        return None
    try:
        return found.decode("utf-8")
    except UnicodeDecodeError:
        return None
