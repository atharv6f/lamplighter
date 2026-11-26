"""The process the crash tests kill. It writes to a real store until it dies.

Run as `python -m tests.crash.child MODE FOLDER REPORT SEED` from the repo's
root. In the random mode it loops forever over the four kinds of write.
Each planted mode does one piece of work and kills itself at a named point,
from a trace callback on its connection, so the code under test has no hook
in it.

After each write returns, one line goes to the report file with a single
unbuffered os.write, so no line can wait in a buffer when the kill comes.
"""

import asyncio
import json
import os
import random
import signal
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lamplighter.adapters import FileBlobStore, SystemClock
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    delete_session,
    open_database,
)
from lamplighter.ports import (
    Action,
    Begun,
    EffectResult,
    Failure,
    Inbound,
    NewEvent,
    Sender,
    effect_key,
)

AT = "2026-11-26T07:35:12.345Z"
SESSIONS = ("crash-a", "crash-b", "crash-c")
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_BLOB_BYTES = 1024 * 1024


class Reporter:
    """Appends one JSON line per finished write to the report file."""

    def __init__(self, path: Path) -> None:
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def say(self, **fields: object) -> None:
        os.write(self._descriptor, (json.dumps(fields) + "\n").encode())


class Stores:
    """Every store, built the way the app builds them."""

    def __init__(self, folder: Path) -> None:
        connection = open_database(folder / "state.db", 5)
        if isinstance(connection, Failure):
            raise SystemExit(f"open failed: {connection.message}")
        self.connection = connection
        clock = SystemClock()
        self.log = SqliteEventLog(connection, {}, MAX_RECORD_BYTES)
        self.ledger = SqliteEffectLedger(connection, clock, MAX_RECORD_BYTES)
        self.inbox = SqliteInbox(connection, MAX_RECORD_BYTES)
        self.blobs = FileBlobStore(folder / "blobs", clock, MAX_BLOB_BYTES)


def batch(batch_id: str, size: int, text: str = "") -> list[NewEvent]:
    """A batch whose every event says which batch it is in, where, and how big."""
    return [
        NewEvent(
            type="crash_note",
            v=1,
            payload={"batch": batch_id, "place": place, "size": size, "text": text},
            at=AT,
        )
        for place in range(size)
    ]


def crash_action(session_id: str, reply_seq: int) -> Action:
    return Action(
        session_id=session_id,
        reply_seq=reply_seq,
        call_index=0,
        attempt=0,
        tool="write_file",
        args={"path": "notes.txt", "text": "tea"},
        action_class="reversible_write",
        target="file:notes.txt",
        effect_key=effect_key(session_id, reply_seq, 0, 0),
        action_hash="a" * 64,
        outward=False,
    )


def crash_inbound(native_id: str) -> Inbound:
    return Inbound(
        channel="crash",
        native_id=native_id,
        conversation="chat-1",
        sender=Sender(id="user-1", verified=True),
        kind="message",
        text="a made-up message",
        sent_at=AT,
        received_at=AT,
    )


async def random_writes(stores: Stores, report: Reporter, seed: int) -> None:
    """Write at random, forever, and report each write that returned."""
    chance = random.Random(seed)
    listed = await stores.log.sessions(None)
    if isinstance(listed, Failure):
        raise SystemExit(f"sessions failed: {listed.message}")
    last = {info.session_id: info.last_seq for info in listed}
    report.say(op="ready")
    for count in range(1_000_000_000):
        name = f"{seed}-{count}"
        kind = chance.choice(("append", "append", "append", "effect", "accept", "put"))
        if kind == "append":
            session_id = chance.choice(SESSIONS)
            size = chance.randint(1, 5)
            expected = last.get(session_id, 0)
            saved = await stores.log.append(session_id, batch(name, size), expected)
            if isinstance(saved, Failure):
                raise SystemExit(f"append failed: {saved.message}")
            last[session_id] = saved
            report.say(op="append", session=session_id, batch=name, size=size)
        elif kind == "effect":
            await _effect(stores, report, seed * 1_000_000_000 + count)
        elif kind == "accept":
            accepted = await stores.inbox.accept(crash_inbound(name))
            if isinstance(accepted, Failure):
                raise SystemExit(f"accept failed: {accepted.message}")
            report.say(op="accept", native_id=name, inbox_id=accepted.inbox_id)
        else:
            data = chance.randbytes(chance.randint(1, 64 * 1024))
            handle = await stores.blobs.put(data, "application/octet-stream")
            if isinstance(handle, Failure):
                raise SystemExit(f"put failed: {handle.message}")
            report.say(op="put", sha256=handle.sha256, size=handle.size)


async def _effect(stores: Stores, report: Reporter, reply_seq: int) -> None:
    begun = await stores.ledger.begin(crash_action("crash-a", reply_seq), None, None)
    if not isinstance(begun, Begun):
        raise SystemExit(f"begin failed: {begun.message}")
    effect_id = begun.effect.effect_id
    report.say(op="begin", effect_id=effect_id)
    finished = await stores.ledger.finish(
        effect_id, EffectResult(outcome="done", output={"bytes": 3})
    )
    if finished is not None:
        raise SystemExit(f"finish failed: {finished.message}")
    report.say(op="finish", effect_id=effect_id, outcome="done")


def kill_at(statement: str, nth: int) -> Callable[[str], None]:
    """Give a trace callback that kills this process as that statement starts."""
    seen = 0

    def trace(text: str) -> None:
        nonlocal seen
        if text == statement:
            seen += 1
            if seen == nth:
                os.kill(os.getpid(), signal.SIGKILL)

    return trace


def die() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def _die_instead(*_: object) -> None:
    die()


async def planted(mode: str, folder: Path, report: Reporter) -> None:  # noqa: C901
    # One branch per planted kill keeps each one readable next to its test.
    if mode == "first_open_before_commit":
        real_connect = sqlite3.connect

        def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            made = real_connect(*args, **kwargs)
            made.set_trace_callback(kill_at("COMMIT", 1))
            return made

        # The connection is made inside open_database, so the callback can
        # only be put on it by replacing the function that makes it.
        sqlite3.connect = connect  # pyright: ignore[reportAttributeAccessIssue]
        Stores(folder)
        return
    stores = Stores(folder)
    trace = stores.connection.set_trace_callback
    if mode == "append_before_commit":
        trace(kill_at("COMMIT", 1))
        await stores.log.append("crash-a", batch("planted", 3), 0)
    elif mode == "large_append_before_commit":
        trace(kill_at("COMMIT", 1))
        await stores.log.append("crash-a", batch("planted", 5, "x" * 1024 * 1024), 0)
    elif mode == "append_after_return":
        await stores.log.append("crash-a", batch("planted", 3), 0)
        report.say(op="append", session="crash-a", batch="planted", size=3)
        die()
    elif mode == "between_begin_and_finish":
        await stores.ledger.begin(crash_action("crash-a", 7), None, None)
        report.say(op="begin")
        die()
    elif mode == "finish_before_commit":
        begun = await stores.ledger.begin(crash_action("crash-a", 7), None, None)
        assert isinstance(begun, Begun)
        trace(kill_at("COMMIT", 1))
        await stores.ledger.finish(
            begun.effect.effect_id, EffectResult(outcome="done", output={})
        )
    elif mode == "accept_before_commit":
        trace(kill_at("COMMIT", 1))
        await stores.inbox.accept(crash_inbound("planted"))
    elif mode == "put_before_rename":
        # The rename is a plain call with no statement to trace, so the kill
        # is planted by replacing it in this process only.
        os.replace = _die_instead  # pyright: ignore[reportAttributeAccessIssue]
        await stores.blobs.put(b"a blob that never gets its name", "text/plain")
    elif mode in ("delete_before_commit", "delete_after_commit"):
        statement = (
            "COMMIT"
            if mode == "delete_before_commit"
            else ("PRAGMA wal_checkpoint(TRUNCATE)")
        )
        trace(kill_at(statement, 1))
        await delete_session(
            stores.connection, stores.blobs, "crash-a", folder / "export", SystemClock()
        )
    report.say(op="the planted kill did not fire")


def main() -> None:
    mode, folder, report_path, seed = sys.argv[1:5]
    report = Reporter(Path(report_path))
    if mode == "random":
        asyncio.run(random_writes(Stores(Path(folder)), report, int(seed)))
    else:
        asyncio.run(planted(mode, Path(folder), report))


if __name__ == "__main__":
    main()
