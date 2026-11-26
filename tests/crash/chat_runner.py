"""Starts the real chat process, kills it with SIGKILL, and checks what it left.

The child is `tests.chat_child` with the `kills` behaviour. It sends in
the next group of messages at each start, so each group starts a run of
its own. The parent kills it at random moments, starts it again, and
goes on until a start finds every group sent and nothing left to do.
"""

import asyncio
import os
import random
import re
import signal
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.sqlite import (
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    open_database,
)
from lamplighter.adapters.system_clock import SystemClock
from lamplighter.adapters.terminal import REPEAT_LINE
from lamplighter.ports import Failure
from tests.chat_child import SEEDED_ALL, SESSION, STARTED, sent_in
from tests.chat_kit import Outcome
from tests.kill_kit import broken_rules

ROOT = Path(__file__).parents[2]
# How long the child runs before the kill, in seconds. It needs about half a
# second to start, and up to two seconds for a group.
KILL_AFTER_S = (0.2, 1.4)
_PROGRESS = re.compile(r"\[(model call \d+|tool \w+)\]")


@dataclass(frozen=True)
class ChatKillRun:
    """What a run of kills did."""

    kills: int
    starts: int
    events: int
    tool_starts: int


def run_chat_kills(folder: Path, kills: int, seed: int) -> ChatKillRun:
    """Kill the chat `kills` times, then let it finish, and check the store.

    There is a group of messages for each kill and a few more, so the work
    lasts long enough for every kill to land.
    """
    chance = random.Random(seed)
    groups = kills + 4
    store, screen = folder / "store", folder / "screen.txt"
    killed = starts = 0
    while True:
        finishing = (store / SEEDED_ALL).exists()
        starts += 1
        child = _start(store, screen, groups)
        try:
            limit = chance.uniform(*KILL_AFTER_S) if killed < kills else 60
            code = child.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            assert killed < kills, "the child did not finish its work in time"
            killed += 1
            continue
        finally:
            _kill(child)
        assert code == 0, _errors(child)
        if finishing:
            break
    return _checked(store, screen, groups, killed, starts)


def _start(store: Path, screen: Path, groups: int) -> subprocess.Popen[bytes]:
    command = ["-m", "tests.chat_child", str(store), "kills", SESSION, str(groups)]
    with screen.open("ab") as shown:
        return subprocess.Popen(
            [sys.executable, *command],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            stdout=shown,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )


def _kill(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGKILL)
    child.wait()


def _errors(child: subprocess.Popen[bytes]) -> str:
    assert child.stderr is not None
    return child.stderr.read().decode()


def _checked(
    store: Path, screen: Path, groups: int, kills: int, starts: int
) -> ChatKillRun:
    connection = open_database(store / "state.db", 5.0)
    assert not isinstance(connection, Failure), connection
    try:
        ended = asyncio.run(_outcome(connection))
    finally:
        connection.close()
    started = _started(store / STARTED)
    wrong = broken_rules(ended, started, sent_in(groups))
    wrong += shown_in_order(ended, screen.read_text())
    assert wrong == [], wrong
    return ChatKillRun(kills, starts, len(ended.events), len(started))


async def _outcome(connection: sqlite3.Connection) -> Outcome:
    events = await SqliteEventLog(connection, {}, 200_000).read(SESSION, 0)
    pending = await SqliteInbox(connection, 200_000).pending()
    due = await SqliteOutbox(connection, SystemClock(), 200_000).due()
    assert not isinstance(events, Failure), events
    assert not isinstance(pending, Failure), pending
    assert not isinstance(due, Failure), due
    return Outcome(events, pending, due, [])


def _started(record: Path) -> list[tuple[str, str]]:
    if not record.exists():
        return []
    lines = record.read_text().splitlines()
    return [
        (name, arguments) for name, arguments in (line.split("\t") for line in lines)
    ]


def shown_in_order(ended: Outcome, screen: str) -> list[str]:
    """Check that each reply was shown, in log order, and each repeat labelled.

    A copy with the repeat line may be the first one shown, when a kill
    came before the first copy was printed.
    """
    expected = [str(e.payload["text"]) for e in ended.events if e.type == "reply_ready"]
    shown = 0
    labelled = False
    wrong: list[str] = []
    for text in _texts(screen):
        if text == REPEAT_LINE:
            labelled = True
            continue
        if labelled and shown and text == expected[shown - 1]:
            pass
        elif shown < len(expected) and text == expected[shown]:
            shown += 1
        else:
            wrong.append(f"shown {text!r} where {expected[shown : shown + 1]} was due")
        labelled = False
    if shown != len(expected):
        wrong.append(f"{shown} of {len(expected)} replies shown")
    return wrong


def _texts(screen: str) -> list[str]:
    texts: list[str] = []
    for line in screen.splitlines():
        while line.startswith("> "):
            line = line[2:]
        if not line or _PROGRESS.fullmatch(line):
            continue
        texts.append(
            line[1:-1] if line.startswith("[") and line.endswith("]") else line
        )
    return texts
