"""The checks of a work folder that the real chat wrote in, read with the store.

The child's notes are made so that the folder shows which changes
happened: a note's file is there once its write happened, it says "done"
once its edit happened, and each command that ran adds its number as a
line to `launches.txt`.
"""

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.ports import (
    Effect,
    Event,
    Failure,
    canonical_json,
    from_stored,
    read_saved_json,
)
from tests.chat_child import SESSION, note_line, note_name
from tests.kill_kit import changes_wrong
from tests.real_folder import COMMAND_FILE, DECOY, NEW_SUFFIX, contents_wrong, snapshot

_COLUMNS = (
    "effect_id, effect_key, v, state, action, probe, undo, result, resolution,"
    " begun_at, finished_at, resolved_at"
)
_JSON_COLUMNS = ("action", "probe", "undo", "result", "resolution")


def folder_wrong(store: Path, work: Path, outside: Path, groups: int) -> list[str]:
    """Give each rule about changes that the store and the folder break."""
    connection = open_database(store / "state.db", 5.0)
    assert not isinstance(connection, Failure), connection
    try:
        events = _events(connection)
        rows = {row.effect_id: row for row in _rows(connection)}
    finally:
        connection.close()
    found = snapshot(work)
    wrong = changes_wrong(events, rows, _changed(found, groups))
    wrong += contents_wrong(found, rows, {})
    if snapshot(outside) != {"decoy.txt": DECOY.encode()}:
        wrong.append("a file outside the work folder changed")
    for name in found:
        # A write cut by the kill may leave its unfinished file, until the
        # next write of that file. The edit is a note's last write.
        if name.endswith(NEW_SUFFIX) and found.get(
            name.removesuffix(NEW_SUFFIX), b""
        ).startswith(b"done"):
            wrong.append(f"{name} is left after its file was written again")
    return wrong


def _changed(found: dict[str, bytes], groups: int) -> list[tuple[str, str]]:
    launched = found.get(COMMAND_FILE, b"").decode().splitlines()
    changed: list[tuple[str, str]] = []
    for number in range(groups):
        name = note_name(number)
        text = found.get(name)
        if text is not None:
            written = {"path": name, "content": f"note {number}\n"}
            changed.append(("write_file", canonical_json(written)))
        if text == f"done {number}\n".encode():
            edited = {"path": name, "old_text": "note", "new_text": "done"}
            changed.append(("edit_file", canonical_json(edited)))
        command = canonical_json({"command": note_line(number)})
        changed += [("shell", command)] * launched.count(str(number))
    return changed


def _events(connection: sqlite3.Connection) -> Sequence[Event]:
    read = asyncio.run(SqliteEventLog(connection, {}, 200_000).read(SESSION, 0))
    assert not isinstance(read, Failure), read
    return read


def _rows(connection: sqlite3.Connection) -> list[Effect]:
    # Every row, not only the open ones the port lists, read as the
    # adapter reads one.
    found = connection.execute(
        f"SELECT {_COLUMNS} FROM effects ORDER BY effect_id"
    ).fetchall()
    names = [name.strip() for name in _COLUMNS.split(",")]
    rows: list[Effect] = []
    for row in found:
        plain = dict(zip(names, row, strict=True))
        for column in _JSON_COLUMNS:
            if plain[column] is not None:
                plain[column] = read_saved_json(plain[column])
        rows.append(from_stored(Effect, plain))
    return rows
