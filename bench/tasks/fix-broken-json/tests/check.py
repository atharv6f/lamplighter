"""data/config.json parses, and holds exactly the original values."""

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


import json

try:
    found = json.loads(text("data/config.json"))
except ValueError:
    fail("data/config.json does not parse")
expected = json.loads((TESTS / "config.json").read_text(encoding="utf-8"))
if found != expected:
    fail("data/config.json holds other values")
