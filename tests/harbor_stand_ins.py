"""Stand-ins for Docker and for the start script, so the runner runs with neither.

Both note each call as one line in one shared file, so a test can read
the order of what happened.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

_DOCKER = """#!/bin/sh
here=$(dirname "$0")
echo "docker $*" >>"$here/calls"
case "$1 $2" in
  "version --format") [ -e "$here/down" ] && { echo "Cannot connect" >&2; exit 1; }
    echo 29.0.0 ;;
  "container ls") cat "$here/containers" ;;
  "network ls") cat "$here/networks" ;;
  *) [ -e "$here/refuse" ] && { echo "Made up: refused" >&2; exit 1; } ;;
esac
exit 0
"""
_START = """#!/bin/sh
here=@HERE@
echo "start $*" >>"$here/calls"
echo "made-up output of $1"
if [ -e "$here/hold" ]; then sleep "$(cat "$here/hold")" & fi
if [ -e "$here/exit_code" ]; then exit "$(cat "$here/exit_code")"; fi
exit 0
"""


@dataclass
class StandIns:
    """The folder of the two stand-ins, the start script's path, and their calls."""

    folder: Path

    @property
    def start_script(self) -> Path:
        return self.folder / "start.sh"

    def calls(self) -> list[str]:
        path = self.folder / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def set(self, name: str, text: str = "") -> None:
        (self.folder / name).write_text(text)

    def clear(self, name: str) -> None:
        (self.folder / name).unlink(missing_ok=True)


def stand_ins(folder: Path, monkeypatch: pytest.MonkeyPatch) -> StandIns:
    """Put a stand-in `docker` first on the PATH, and make a stand-in start script."""
    folder.mkdir()
    docker = folder / "docker"
    docker.write_text(_DOCKER)
    docker.chmod(0o755)
    (folder / "start.sh").write_text(_START.replace("@HERE@", str(folder)))
    for name in ("containers", "networks"):
        (folder / name).write_text("")
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    return StandIns(folder)
