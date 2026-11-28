"""errors.txt holds the count of ERROR lines for each hour that has one."""

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


found = [line.strip() for line in text("errors.txt").splitlines() if line.strip()]
expected = (TESTS / "errors.txt").read_text().splitlines()
if found != expected:
    fail("errors.txt does not hold the expected counts")
