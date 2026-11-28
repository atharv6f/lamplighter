"""report.py prints the expected table of two settings files, with the agent's two modules."""

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
for name in ["parser.py", "formatter.py"]:
    shutil.copy(APP / name, place / name)
shutil.copy(TESTS / "report.py", place / "report.py")
for name in ["settings.txt", "sample.txt"]:
    done = subprocess.run(
        ["python3", "report.py", str(TESTS / name)],
        cwd=place, capture_output=True, text=True, timeout=30,
    )
    expected = (TESTS / f"{name}.expected").read_text(encoding="utf-8")
    if done.returncode != 0 or done.stdout != expected:
        fail(f"the table of {name} is not the expected one:\n{done.stdout}")
