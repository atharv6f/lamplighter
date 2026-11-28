"""wordcount.py prints the words, and with --lines the lines too, on the check's files."""

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


import subprocess

EXPECTED = {'poem.txt': (11, 4), 'no_newline.txt': (8, 1), 'empty.txt': (0, 0)}


def run(*args: str) -> str:
    done = subprocess.run(
        ["python3", str(APP / "wordcount.py"), *args],
        capture_output=True, text=True, timeout=30,
    )
    if done.returncode != 0:
        fail(f"wordcount.py {' '.join(args)} ended with {done.returncode}")
    return done.stdout


for name, (words, lines) in EXPECTED.items():
    path = str(TESTS / name)
    if run(path) != f"words: {words}\n":
        fail(f"without the flag, the output for {name} changed")
    both = f"words: {words}\nlines: {lines}\n"
    if run("--lines", path) != both or run(path, "--lines") != both:
        fail(f"with --lines, the output for {name} is not {both!r}")
