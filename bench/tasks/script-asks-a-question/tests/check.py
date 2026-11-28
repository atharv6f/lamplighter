"""installed.flag is the file setup.sh writes when it has set up the tool."""

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


if text("installed.flag") != "made-up tool 1.0, set up\n":
    fail("installed.flag is not the one setup.sh writes")
