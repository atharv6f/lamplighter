"""latest.txt is a regular file: the report and one more line."""

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


if text("latest.txt") != (TESTS / "latest.txt").read_text(encoding="utf-8"):
    fail("latest.txt is not the report with the line reviewed")
