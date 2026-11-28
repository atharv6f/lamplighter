"""Each of the twelve cards has its name from the table, and nothing else changed."""

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

rows = csv.DictReader((TESTS / "names.csv").open(encoding="utf-8"))
for row in rows:
    path = f"cards/card{row['card']}.txt"
    if text(path) != f"card {row['card']}\nname: {row['name']}\n":
        fail(f"{path} is not filled in")
