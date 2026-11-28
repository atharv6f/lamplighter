"""merged.csv holds one row per address, by the stated rules."""

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


import csv

found = [row for row in csv.reader(text("merged.csv").splitlines()) if row]
expected = list(csv.reader((TESTS / "merged.csv").read_text().splitlines()))
if [[cell.strip() for cell in row] for row in found] != expected:
    fail("merged.csv does not hold the expected rows")
