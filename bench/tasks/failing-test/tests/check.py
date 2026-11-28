"""The check's own copy of the tests, and one more, pass against orders.py."""

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


import shutil
import subprocess
import tempfile

place = Path(tempfile.mkdtemp())
shutil.copy(APP / "orders.py", place / "orders.py")
shutil.copy(TESTS / "test_orders.py", place / "test_orders.py")
shutil.copy(TESTS / "test_more.py", place / "test_more.py")
done = subprocess.run(
    ["python3", "-m", "unittest", "test_orders", "test_more"],
    cwd=place, capture_output=True, text=True, timeout=60,
)
if done.returncode != 0:
    fail("the check's copy of the tests does not pass:\n" + done.stderr)
