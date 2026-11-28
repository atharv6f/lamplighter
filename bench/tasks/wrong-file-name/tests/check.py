"""config/app.yml has debug set to false, and config/ holds no new file."""

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


names = sorted(p.name for p in (APP / "config").iterdir())
if names != ["app.yml", "logging.yml"]:
    fail(f"config/ holds {names}")
if text("config/app.yml") != (TESTS / "app.yml").read_text(encoding="utf-8"):
    fail("config/app.yml is not the expected text")
