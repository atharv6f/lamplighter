"""The code revision: a commit, a commit with a mark for its changes, or unknown."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from lamplighter.app.code_revision import (
    REPO_ROOT,
    UNKNOWN,
    changes_mark,
    code_revision,
)
from lamplighter.ports import canonical_json

COMMIT = re.compile(r"[0-9a-f]{40}")
CHANGED = re.compile(r"([0-9a-f]{40})\+dirty\.([0-9a-f]{12})")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_AUTHOR_NAME": "Made Up",
            "GIT_AUTHOR_EMAIL": "made-up@example.invalid",
            "GIT_COMMITTER_NAME": "Made Up",
            "GIT_COMMITTER_EMAIL": "made-up@example.invalid",
        },
    )
    return done.stdout


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "code.py").write_text("x = 1\n")
    (root / "bench").mkdir()
    (root / "bench" / "rule.toml").write_text("a = 1\n")
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "uv.lock").write_text("lock\n")
    (root / "README.md").write_text("Made up.\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    return root


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def test_a_clean_checkout_gives_its_commit(checkout: Path) -> None:
    found = code_revision(checkout)
    assert COMMIT.fullmatch(found) and found == head(checkout)


def test_a_changed_file_under_the_code_paths_gives_the_commit_and_a_mark(
    checkout: Path,
) -> None:
    for path in ("src/pkg/code.py", "bench/rule.toml", "pyproject.toml", "uv.lock"):
        before = (checkout / path).read_text()
        (checkout / path).write_text(before + "# changed\n")
        found = CHANGED.fullmatch(code_revision(checkout))
        assert found is not None and found.group(1) == head(checkout), path
        (checkout / path).write_text(before)
    assert code_revision(checkout) == head(checkout)


def test_a_change_outside_the_code_paths_is_not_a_change_of_code(
    checkout: Path,
) -> None:
    (checkout / "README.md").write_text("Changed.\n")
    (checkout / "notes.txt").write_text("New.\n")
    assert code_revision(checkout) == head(checkout)


def test_the_same_changes_give_the_same_mark_and_others_give_another(
    checkout: Path,
) -> None:
    code = checkout / "src" / "pkg" / "code.py"
    code.write_text("x = 2\n")
    first = code_revision(checkout)
    assert code_revision(checkout) == first
    code.write_text("x = 3\n")
    assert code_revision(checkout) not in (first, head(checkout))
    code.write_text("x = 2\n")
    assert code_revision(checkout) == first


def test_the_mark_is_the_hash_of_each_changed_path_and_what_it_holds(
    checkout: Path,
) -> None:
    code = checkout / "src" / "pkg" / "code.py"
    code.write_text("x = 2\n")
    (checkout / "src" / "pkg" / "new file.py").write_text("y = 1\n")
    (checkout / "bench" / "rule.toml").unlink()
    entries = [
        ["bench/rule.toml", "deleted"],
        ["src/pkg/code.py", hashlib.sha256(b"x = 2\n").hexdigest()],
        ["src/pkg/new file.py", hashlib.sha256(b"y = 1\n").hexdigest()],
    ]
    expected = hashlib.sha256(canonical_json(entries).encode()).hexdigest()[:12]
    assert code_revision(checkout) == f"{head(checkout)}+dirty.{expected}"


def test_a_staged_change_counts_as_a_change(checkout: Path) -> None:
    (checkout / "src" / "pkg" / "code.py").write_text("x = 2\n")
    git(checkout, "add", "src/pkg/code.py")
    assert CHANGED.fullmatch(code_revision(checkout))


def test_a_renamed_file_is_one_deleted_path_and_one_new_one(checkout: Path) -> None:
    git(checkout, "mv", "src/pkg/code.py", "src/pkg/renamed code.py")
    mark = changes_mark(checkout, ["src/pkg/code.py", "src/pkg/renamed code.py"])
    assert code_revision(checkout) == f"{head(checkout)}+dirty.{mark}"


def test_a_new_folder_that_is_a_repository_of_its_own_is_one_entry(
    checkout: Path,
) -> None:
    inner = checkout / "src" / "inner"
    inner.mkdir()
    (inner / "a.py").write_text("a = 1\n")
    git(inner, "init", "-q")
    first = code_revision(checkout)
    assert first == f"{head(checkout)}+dirty.{changes_mark(checkout, ['src/inner'])}"
    # A change inside it is not seen: a known limit.
    (inner / "a.py").write_text("a = 2\n")
    assert code_revision(checkout) == first


def test_a_folder_that_is_not_a_git_checkout_is_unknown(tmp_path: Path) -> None:
    assert code_revision(tmp_path) == UNKNOWN


def test_a_folder_inside_another_repository_is_unknown(checkout: Path) -> None:
    # The code would then sit inside some other repository, whose commit
    # says nothing about it.
    assert code_revision(checkout / "src") == UNKNOWN


def test_a_checkout_with_no_commit_is_unknown(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    assert code_revision(tmp_path) == UNKNOWN


def test_with_no_git_on_the_path_it_is_unknown(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert code_revision(checkout) == UNKNOWN


def test_a_git_folder_named_by_the_callers_environment_is_not_read(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert code_revision(checkout) == head(checkout)


def test_reading_the_state_never_writes_to_the_checkout(checkout: Path) -> None:
    # A file whose times changed and whose bytes did not is one that a
    # plain `git status` refreshes in the index, and so writes the index.
    code = checkout / "src" / "pkg" / "code.py"
    later = code.stat().st_mtime + 100
    os.utime(code, (later, later))
    index = checkout / ".git" / "index"
    before = index.stat().st_mtime_ns, index.read_bytes()
    code_revision(checkout)
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before


def test_the_root_is_the_folder_that_holds_the_package() -> None:
    assert (REPO_ROOT / "src" / "lamplighter" / "app" / "code_revision.py").is_file()
    assert (REPO_ROOT / "pyproject.toml").is_file()
