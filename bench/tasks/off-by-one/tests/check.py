"""pages gives every item once, in pages of the size asked, on the check's cases."""

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
import subprocess

CASES = [
    [list(range(1, 8)), 3, [[1, 2, 3], [4, 5, 6], [7]]],
    [list(range(1, 7)), 3, [[1, 2, 3], [4, 5, 6]]],
    [["a"], 5, [["a"]]],
    [[], 3, []],
    [list(range(10)), 1, [[n] for n in range(10)]],
]
# The agent's module runs in a process of its own, so that it cannot
# end this check early.
RUN = (
    "import json, sys\n"
    "sys.path.insert(0, '/app')\n"
    "from paginate import pages\n"
    "cases = json.load(sys.stdin)\n"
    "print(json.dumps([pages(items, size) for items, size, _ in cases]))\n"
)
done = subprocess.run(
    ["python3", "-c", RUN], input=json.dumps(CASES),
    capture_output=True, text=True, timeout=60, cwd="/tmp",
)
if done.returncode != 0 or not done.stdout.strip():
    fail("paginate.py could not be run:\n" + done.stderr)
found = json.loads(done.stdout.strip().splitlines()[-1])
if len(found) != len(CASES):
    fail("pages was not run on every case")
for (items, size, expected), got in zip(CASES, found):
    if got != expected:
        fail(f"pages({items}, {size}) is {got}, not {expected}")
