"""A real Phoenix of a test's own, started by the repo's script, and its span list.

Each Phoenix gets a temp folder and two free ports, in a process group of
its own. `uv` starts Phoenix as a child, so the whole group is killed at
the end: a kill of the script alone would leave Phoenix running. The
owner's own Phoenix and its folder are never touched.
"""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tests.group_kill import kill_group

START = Path(__file__).parents[1] / "scripts" / "phoenix" / "start.sh"
VERSION = "22.1.0"
# A first start on a slow machine compiles every package once.
_STARTS_WITHIN_S = 180.0
# No proxy, so a question to this Mac never leaves it.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Phoenix:
    """One running Phoenix: its address, its folder and its two ports."""

    url: str
    folder: Path
    port: int
    grpc_port: int
    process: subprocess.Popen[bytes]

    def get(self, path: str) -> bytes:
        return asked(self.url, path)

    def spans(self, project: str) -> list[dict[str, Any]]:
        """Give every span Phoenix lists in `project`."""
        return listed_spans(self.url, project)

    def names(self, project: str = "lamplighter") -> dict[str, str]:
        """Give the name of each span in `project`, by its id."""
        return {row["context"]["span_id"]: row["name"] for row in self.spans(project)}

    def stop(self) -> None:
        kill_group(self.process)


def asked(url: str, path: str) -> bytes:
    """Give the body of Phoenix's answer to a GET of `path`."""
    with _DIRECT.open(url + path, timeout=10) as answer:
        return cast(bytes, answer.read())


def listed_spans(url: str, project: str) -> list[dict[str, Any]]:
    """Give every span the Phoenix at `url` lists in `project`, page by page."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = "limit=1000" + (f"&cursor={cursor}" if cursor else "")
        try:
            page = json.loads(asked(url, f"/v1/projects/{project}/spans?{query}"))
        except urllib.error.HTTPError as error:
            # Phoenix makes a project as it stores its first span.
            if error.code == 404:
                return rows
            raise
        rows += cast(list[dict[str, Any]], page["data"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows


def launch(
    folder: Path,
    port: int,
    grpc_port: int,
    env: Mapping[str, str] | None = None,
) -> Phoenix:
    """Start Phoenix through the script and wait until it answers."""
    log = (folder.parent / f"{folder.name}.log").open("ab")
    process = subprocess.Popen(
        [str(START), str(folder), str(port), str(grpc_port)],
        env=dict(os.environ if env is None else env),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    made = Phoenix(f"http://127.0.0.1:{port}", folder, port, grpc_port, process)
    deadline = time.monotonic() + _STARTS_WITHIN_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Phoenix ended at its start: {process.returncode}")
        try:
            made.get("/arize_phoenix_version")
            return made
        except OSError:
            time.sleep(0.2)
    made.stop()
    raise AssertionError("Phoenix did not answer in time")


@contextmanager
def running_phoenix(
    folder: Path, env: Mapping[str, str] | None = None
) -> Generator[Phoenix]:
    """Run a Phoenix of the test's own for the length of the block."""
    made = launch(folder, free_port(), free_port(), env)
    try:
        yield made
    finally:
        made.stop()
