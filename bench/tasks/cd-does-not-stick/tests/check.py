"""The build's result is in project/sub/out/result.txt."""

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


if text("project/sub/out/result.txt") != "bolt\ngear\nspring\nparts: 3\n":
    fail("project/sub/out/result.txt is not the build's result")
