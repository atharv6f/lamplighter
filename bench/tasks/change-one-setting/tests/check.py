"""settings.ini must be the original with only the port changed."""

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


expected = (TESTS / "settings.ini").read_text(encoding="utf-8")
if text("settings.ini").rstrip("\n") != expected.rstrip("\n"):
    fail("settings.ini is not the expected text")
