"""No .py file holds fetch_rows, and the package works with load_rows."""

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


import importlib

for path in APP.rglob("*.py"):
    if "fetch_rows" in path.read_text(encoding="utf-8", errors="replace"):
        fail(f"{path} still holds fetch_rows")
sys.path.insert(0, str(APP))
rowkit = importlib.import_module("rowkit")
report = importlib.import_module("rowkit.report")
sample = "alpha\n\n  beta \ngamma\n"
if rowkit.load_rows(sample) != ["alpha", "beta", "gamma"]:
    fail("load_rows gives the wrong rows")
if rowkit.count_rows(sample) != 3 or rowkit.first_row(sample) != "alpha":
    fail("count_rows or first_row is wrong")
if report.report(sample) != "3 rows, first: alpha":
    fail("report is wrong")
