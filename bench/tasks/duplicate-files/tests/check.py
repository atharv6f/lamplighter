"""duplicates.txt lists the groups of identical files, and every file is still there."""

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

found = [line.split() for line in text("duplicates.txt").splitlines() if line.strip()]
expected = [line.split() for line in (TESTS / "duplicates.txt").read_text().splitlines()]
if found != expected:
    fail("duplicates.txt does not list the expected groups")
for line in (TESTS / "sums.txt").read_text().splitlines():
    digest, path = line.split()
    place = APP / path
    if place.is_symlink() or not place.is_file():
        fail(f"{path} is gone")
    if hashlib.sha256(place.read_bytes()).hexdigest() != digest:
        fail(f"{path} was changed")
