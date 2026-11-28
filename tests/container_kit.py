"""A container of the pinned image for tests, and the root call a trial gets into it.

The tests' temp folder is mounted at the same path inside, as a trial's
shared folder is. The container has no network and Docker's small first
process, as the agent's container of a trial has.
"""

import asyncio
import os
import secrets
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.container import AsRoot

IMAGE = (
    "python:3.13-slim-bookworm"
    "@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3"
)
# A made-up user for a run as root, whose protection rests on a command
# user that is not root.
_STAND_IN = 1001


def command_user() -> tuple[int, int]:
    """Give the user and group a command runs as: the runner's, or a stand-in."""
    if os.getuid() == 0:
        return _STAND_IN, _STAND_IN
    return os.getuid(), os.getgid()


def open_to_command_user(work: Path, base: Path) -> None:
    """Make `work` the command user's, and each folder above it up to `base` passable.

    Only a run as root needs it: the runner's own folders are already the
    runner's, and pytest makes its folders open to their owner only.
    """
    if os.getuid() != 0:
        return
    user, group = command_user()
    os.chown(work, user, group)
    for folder in (work.parent, *work.parent.parents):
        os.chmod(folder, os.stat(folder).st_mode | 0o711)
        if folder == base:
            break


@dataclass(frozen=True)
class RunningContainer:
    """One running container, by its name."""

    name: str

    def as_root(self) -> AsRoot:
        """Give the call shape of Harbor's exec with no user: `bash -c` as root."""

        async def as_root(line: str, cwd: str, seconds: int) -> tuple[int, str] | None:
            argv = ["docker", "exec", "-u", "0:0", "-w", cwd, self.name]
            process = await asyncio.create_subprocess_exec(
                *argv,
                "bash",
                "-c",
                line,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                out, _ = await asyncio.wait_for(process.communicate(), seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
                return None
            return process.returncode or 0, out.decode(errors="replace")

        return as_root

    def inside(self, line: str, user: str = "0:0") -> str:
        """Run one bash line in the container, from outside the adapter."""
        done = subprocess.run(
            ["docker", "exec", "-u", user, self.name, "bash", "-c", line],
            capture_output=True,
            check=False,
        )
        return (done.stdout + done.stderr).decode(errors="replace")

    def is_alive(self, pid: int) -> bool:
        """Say whether a process of the container is alive. A zombie counts as dead."""
        stat = self.inside(f"cat /proc/{pid}/stat 2>/dev/null")
        state = stat.rsplit(") ", 1)[-1][:1] if ") " in stat else ""
        return state not in ("", "Z")

    def kill(self, pid: int) -> None:
        """Kill a process of the container, from outside the adapter."""
        self.inside(f"kill -KILL {pid} 2>/dev/null")

    def stop(self) -> None:
        """Stop the container, so that its processes end and it stays listed."""
        subprocess.run(["docker", "stop", "-t", "0", self.name], capture_output=True)

    def remove(self) -> None:
        """Remove the container, running or not."""
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


@contextmanager
def started_container(base: Path, *, init: bool = True) -> Generator[RunningContainer]:
    """Start a container of the pinned image with `base` mounted at the same path.

    With `init` false, its first process is a plain `sleep`, which collects
    no dead process.
    """
    name = f"lamplighter-test-{secrets.token_hex(6)}"
    first = ["--init"] if init else []
    subprocess.run(
        [
            *("docker", "run", "-d", "--rm", "--network", "none", *first),
            *("--name", name, "-v", f"{base}:{base}", IMAGE, "sleep", "infinity"),
        ],
        check=True,
        capture_output=True,
    )
    container = RunningContainer(name)
    try:
        yield container
    finally:
        container.remove()
