"""price.txt is the word unreachable, and last year's list is unchanged."""

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


if text("price.txt").strip() != "unreachable":
    fail("price.txt does not say unreachable")
unchanged("sums.txt")
