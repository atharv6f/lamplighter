"""config/service.conf has only timeout changed, and no other file changed."""

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


import hashlib


def unchanged(sums: str) -> None:
    for line in (TESTS / sums).read_text().splitlines():
        digest, path = line.split()
        place = APP / path
        if place.is_symlink() or not place.is_file():
            fail(f"{path} is gone")
        if hashlib.sha256(place.read_bytes()).hexdigest() != digest:
            fail(f"{path} was changed")


expected = (TESTS / "service.conf").read_text(encoding="utf-8")
if text("config/service.conf") != expected:
    fail("config/service.conf is not the original with only timeout changed")
unchanged("sums.txt")
names = sorted(p.name for p in (APP / "config").iterdir())
if names != ["database.conf", "service.conf"]:
    fail(f"config/ holds {names}")
