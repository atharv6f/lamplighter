"""notes.md must have only the Release section's status changed."""

import sys
from pathlib import Path

APP = Path("/app")
TESTS = Path("/tests")


def fail(why: str) -> None:
    print(why, file=sys.stderr)
    sys.exit(1)


def text(path: str) -> str:
    found = APP / path
    if found.is_symlink() or not found.is_file():
        fail(f"{path} is not a regular file")
    return found.read_text(encoding="utf-8")


expected = (TESTS / "notes.md").read_text(encoding="utf-8")
if text("notes.md").rstrip("\n") != expected.rstrip("\n"):
    fail("notes.md is not the expected text")
