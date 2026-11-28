"""answer.txt holds the id of the one request to /pay that failed with 500."""

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


if text("answer.txt").strip() != "r5f8b797c":
    fail("answer.txt does not hold the failed request's id")
