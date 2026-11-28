"""Every file of inbox/ is in archive/<year>/<month>/ with its bytes, and no more."""

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


inbox = APP / "inbox"
if inbox.is_dir() and any(inbox.iterdir()):
    fail("inbox/ still holds a file")
expected = set()
for line in (TESTS / "places.txt").read_text().splitlines():
    old, new = line.split()
    expected.add(new)
    if text(new) != (TESTS / old).read_text(encoding="utf-8"):
        fail(f"{new} does not hold what {old} held")
archive = APP / "archive"
found = {str(p.relative_to(APP)) for p in archive.rglob("*") if not p.is_dir()}
if found != expected:
    fail(f"archive/ holds other files: {sorted(found - expected)}")
