"""What must hold in a store after any kill, checked from the outside."""

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lamplighter.adapters.sqlite import open_database
from lamplighter.ports import Failure, is_hash

STATES = {"intended", "done", "failed", "unknown", "resolved", "undone"}


@dataclass
class Reported:
    """What the child said it saved, read from its report file."""

    appends: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    begun: set[int] = field(default_factory=set[int])
    finished: dict[int, str] = field(default_factory=dict[int, str])
    accepted: dict[str, int] = field(default_factory=dict[str, int])
    puts: dict[str, int] = field(default_factory=dict[str, int])
    runs: int = 0
    runs_that_wrote: int = 0


def read_report(path: Path) -> Reported:
    """Read every line the children wrote, and count the runs that wrote."""
    reported = Reported()
    wrote_since_ready = False
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        op = entry["op"]
        if op == "ready":
            reported.runs += 1
            wrote_since_ready = False
            continue
        if not wrote_since_ready:
            reported.runs_that_wrote += 1
            wrote_since_ready = True
        if op == "append":
            reported.appends.append(entry)
        elif op == "begin":
            reported.begun.add(entry["effect_id"])
        elif op == "finish":
            reported.finished[entry["effect_id"]] = entry["outcome"]
        elif op == "accept":
            reported.accepted[entry["native_id"]] = entry["inbox_id"]
        elif op == "put":
            reported.puts[entry["sha256"]] = entry["size"]
    return reported


def check_store(folder: Path, reported: Reported) -> int:
    """Open the store as a restart would, check every promise, and give the batch count.

    Raises AssertionError, naming what broke, when any promise does not hold.
    """
    connection = open_database(folder / "state.db", 5)
    assert not isinstance(connection, Failure), connection
    try:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        batches = _check_events(connection, reported)
        _check_effects(connection, reported)
        _check_inbox(connection, reported)
    finally:
        connection.close()
    _check_blobs(folder / "blobs", reported)
    return batches


def _check_events(connection: sqlite3.Connection, reported: Reported) -> int:
    rows = connection.execute(
        "SELECT session_id, seq, pos, payload FROM events ORDER BY session_id, seq"
    ).fetchall()
    by_session: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_batch: dict[str, list[tuple[str, int, dict[str, Any]]]] = defaultdict(list)
    for session_id, seq, pos, payload in rows:
        by_session[session_id].append((seq, pos))
        plain = json.loads(payload)
        by_batch[plain["batch"]].append((session_id, seq, plain))
    for session_id, places in by_session.items():
        seqs = [seq for seq, _ in places]
        assert seqs == list(range(1, len(seqs) + 1)), f"a gap in seq in {session_id}"
        positions = [pos for _, pos in places]
        assert positions == sorted(set(positions)), f"pos not rising in {session_id}"
    for batch_id, events in by_batch.items():
        size = events[0][2]["size"]
        assert len(events) == size, f"batch {batch_id} has {len(events)} of {size}"
        assert len({session for session, _, _ in events}) == 1
        seqs = sorted(seq for _, seq, _ in events)
        assert seqs == list(range(seqs[0], seqs[0] + size)), f"batch {batch_id} split"
        places = sorted(plain["place"] for _, _, plain in events)
        assert places == list(range(size)), f"batch {batch_id} has the wrong places"
    for entry in reported.appends:
        assert entry["batch"] in by_batch, f"a reported batch is lost: {entry}"
    return len(by_batch)


def _check_effects(connection: sqlite3.Connection, reported: Reported) -> None:
    states = dict(connection.execute("SELECT effect_id, state FROM effects").fetchall())
    for effect_id, state in states.items():
        assert state in STATES, f"effect {effect_id} is in state {state}"
    for effect_id in reported.begun:
        assert effect_id in states, f"a reported intent is lost: {effect_id}"
    for effect_id, outcome in reported.finished.items():
        assert states.get(effect_id) == outcome, (
            f"a reported finish is lost: {effect_id}"
        )


def _check_inbox(connection: sqlite3.Connection, reported: Reported) -> None:
    pairs = connection.execute(
        "SELECT channel, native_id, inbox_id FROM inbox"
    ).fetchall()
    keys = [(channel, native_id) for channel, native_id, _ in pairs]
    assert len(keys) == len(set(keys)), "one message is in the inbox twice"
    ids = {native_id: inbox_id for _, native_id, inbox_id in pairs}
    for native_id, inbox_id in reported.accepted.items():
        assert ids.get(native_id) == inbox_id, f"a reported accept is lost: {native_id}"


def _check_blobs(folder: Path, reported: Reported) -> None:
    for sha256, size in reported.puts.items():
        data = (folder / sha256).read_bytes()
        assert len(data) == size, f"a reported blob is cut short: {sha256}"
    if not folder.exists():
        return
    for path in folder.iterdir():
        if is_hash(path.name):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == path.name, f"a file under a blob's name is partial: {path}"
