"""install.sh, run three times under a new PREFIX, ends well each time and once."""

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


import os
import subprocess
import tempfile

prefix = Path(tempfile.mkdtemp()) / "prefix"
for run in range(3):
    done = subprocess.run(
        ["bash", str(APP / "install.sh")],
        env={**os.environ, "PREFIX": str(prefix)},
        capture_output=True, text=True, timeout=60,
    )
    if done.returncode != 0:
        fail(f"run {run + 1} of install.sh ended with {done.returncode}")
tool = prefix / "bin" / "tool"
if tool.read_text() != (TESTS / "tool.sh").read_text():
    fail("bin/tool is not the tool")
link = prefix / "bin" / "mt"
if not link.is_symlink() or link.resolve() != tool.resolve():
    fail("bin/mt is not a link to the tool")
line = f'export PATH="{prefix}/bin:$PATH"'
if (prefix / "etc" / "profile").read_text().splitlines() != [line]:
    fail("etc/profile does not hold its line exactly once")
