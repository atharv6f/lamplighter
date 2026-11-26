"""Exports, deletes and measures whole sessions in the store's SQLite file.

These sit outside the ports: deleting a session touches three tables in one
transaction, and only the owner of the file can do that.
"""

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lamplighter.adapters.drive import flush_folder, is_full, write_flushed
from lamplighter.adapters.sqlite.errors import damaged
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    BlobHandle,
    BlobStore,
    Clock,
    Failure,
    is_hash,
    is_id,
    read_saved_json,
    saved_json,
    utc_text,
)

_SOURCE = "retention/sqlite"

EXPORT_FORMAT = 1

# Each table's rows of one session, and the columns that hold saved JSON.
# An export writes those columns as nested values, not as text inside text.
_SESSION_ROWS = {
    "events.jsonl": "SELECT * FROM events WHERE session_id = ? ORDER BY seq",
    "effects.jsonl": "SELECT * FROM effects WHERE session_id = ? ORDER BY effect_id",
    "inbox.jsonl": (
        "SELECT * FROM inbox WHERE routed_session_id = ? ORDER BY inbox_id"
    ),
}
_JSON_COLUMNS = frozenset(
    {"payload", "action", "probe", "undo", "result", "resolution", "inbound"}
)

# Every saved JSON value in the file, for the list of blob hashes in use.
_EVERY_JSON = """
SELECT payload FROM events
UNION ALL SELECT action FROM effects
UNION ALL SELECT probe FROM effects WHERE probe IS NOT NULL
UNION ALL SELECT undo FROM effects WHERE undo IS NOT NULL
UNION ALL SELECT result FROM effects WHERE result IS NOT NULL
UNION ALL SELECT resolution FROM effects WHERE resolution IS NOT NULL
UNION ALL SELECT inbound FROM inbox
"""

_HEX_RUN = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


@dataclass(frozen=True, slots=True)
class ExportReport:
    """What an export wrote: the folder, the row counts and the blobs copied."""

    folder: str
    last_seq: int
    events: int
    effects: int
    inbox: int
    blobs: tuple[str, ...]
    files_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DeleteReport:
    """What a delete did. `log_cleared` is false when another process was busy.

    In that case the deleted rows stay readable in the write-ahead log file
    until the next open of the store clears it.
    """

    export: ExportReport
    log_cleared: bool


@dataclass(frozen=True, slots=True)
class TableSize:
    """The rows of one table and the bytes of their saved JSON."""

    table: str
    rows: int
    json_bytes: int


@dataclass(frozen=True, slots=True)
class SessionSize:
    """The events of one session and the bytes of their payloads."""

    session_id: str
    events: int
    json_bytes: int


@dataclass(frozen=True, slots=True)
class SizeReport:
    """How large the store's file is and what fills it."""

    file_bytes: int
    log_bytes: int
    page_bytes: int
    pages: int
    free_pages: int
    tables: tuple[TableSize, ...]
    largest_sessions: tuple[SessionSize, ...]


@dataclass(frozen=True, slots=True)
class _SessionTexts:
    """The three export files of one session, as text, and what they hold."""

    texts: dict[str, str]
    counts: dict[str, int]
    last_seq: int
    handles: tuple[BlobHandle, ...]
    named: frozenset[str]


async def export_session(
    connection: sqlite3.Connection,
    blobs: BlobStore,
    session_id: str,
    folder: Path,
    clock: Clock,
) -> ExportReport | Failure:
    """Write one session into a new folder: its rows and the blobs they point at.

    The rows are read in one transaction, so the copy is of one moment. Each
    blob is read through the port, so its hash is checked on the way. The
    manifest is written last, so a folder with a manifest holds a whole
    export. Every file is forced to the drive. A row that names a stored
    blob by its hash alone, without its handle, gives `needs_user` before
    anything is written, because that blob could not be copied and checked.
    """
    if not is_id(session_id):
        return _invalid("The session id breaks the id rule.")
    if folder.exists():
        return _invalid(f"The export folder {folder} already exists.")
    rows = read(connection, _SOURCE, lambda c: _session_texts(c, session_id))
    if isinstance(rows, Failure):
        return rows
    if rows.last_seq == 0:
        return _invalid(f"There is no session {session_id}.")
    bare = await _blobs_named_by_hash_alone(blobs, rows)
    if isinstance(bare, Failure):
        return bare
    if bare:
        return Failure(
            kind="needs_user",
            effect="none",
            message=f"The session names the blob {bare[0]} by its hash alone, so"
            " the export cannot copy it and check it. Nothing was written.",
            source=_SOURCE,
        )
    copied = await _write_export(blobs, folder, rows)
    if isinstance(copied, Failure):
        return copied
    files_sha256 = {name: _sha256(text) for name, text in rows.texts.items()}
    manifest = {
        "format": EXPORT_FORMAT,
        "session_id": session_id,
        "exported_at": utc_text(clock.now()),
        "last_seq": rows.last_seq,
        "events": rows.counts["events.jsonl"],
        "effects": rows.counts["effects.jsonl"],
        "inbox": rows.counts["inbox.jsonl"],
        "blobs": list(copied),
        "sha256": files_sha256,
    }
    try:
        write_flushed(folder / "manifest.json", (saved_json(manifest) + "\n").encode())
        flush_folder(folder)
    except OSError as error:
        return _os_failure(error)
    return ExportReport(
        folder=str(folder),
        last_seq=rows.last_seq,
        events=rows.counts["events.jsonl"],
        effects=rows.counts["effects.jsonl"],
        inbox=rows.counts["inbox.jsonl"],
        blobs=copied,
        files_sha256=files_sha256,
    )


async def delete_session(
    connection: sqlite3.Connection,
    blobs: BlobStore,
    session_id: str,
    export_to: Path,
    clock: Clock,
) -> DeleteReport | Failure:
    """Export one session, then delete its events, effects and routed inbox rows.

    A session that is unfinished, or has an effect not yet settled, is
    refused, because an open question about the world must be settled
    before its record goes. Inside its transaction the delete builds the
    export's texts again from the rows as they now are. If anything was
    added or changed since the export, it deletes nothing and gives
    `conflict`. Blobs are not touched here: the sweep removes them.
    """
    refused = read(connection, _SOURCE, lambda c: _refusal(c, session_id))
    if refused is not None:
        return refused
    exported = await export_session(connection, blobs, session_id, export_to, clock)
    if isinstance(exported, Failure):
        return exported

    def body(c: sqlite3.Connection) -> int | Failure:
        refused = _refusal(c, session_id)
        if refused is not None:
            return refused
        now = _session_texts(c, session_id)
        if isinstance(now, Failure):
            return now
        hashes_now = {name: _sha256(text) for name, text in now.texts.items()}
        if hashes_now != exported.files_sha256:
            return Failure(
                kind="conflict",
                effect="none",
                message="The session changed after it was exported. Nothing was"
                " deleted. Export it again.",
                source=_SOURCE,
            )
        c.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
        c.execute("DELETE FROM effects WHERE session_id = ?", (session_id,))
        c.execute("DELETE FROM inbox WHERE routed_session_id = ?", (session_id,))
        return now.last_seq

    deleted = write(connection, _SOURCE, body)
    if isinstance(deleted, Failure):
        return deleted
    return DeleteReport(export=exported, log_cleared=_clear_log(connection))


def _clear_log(connection: sqlite3.Connection) -> bool:
    # Folds the log back into the file and empties it, so deleted rows do not
    # stay readable there. It answers busy, and does nothing, while another
    # process is reading or writing. The delete has already happened, so an
    # error here only means the log waits for the next open, which clears it.
    try:
        (busy, _, _) = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error:
        return False
    return busy == 0


def hashes_in_use(connection: sqlite3.Connection) -> frozenset[str] | Failure:
    """Give every run of 64 hex characters in any saved JSON value of the file.

    That is a superset of the blob hashes in use: an id such as an event_id
    can also match. It errs only one way, keeping a blob longer than needed.
    """

    def body(c: sqlite3.Connection) -> frozenset[str]:
        found: set[str] = set()
        for (text,) in c.execute(_EVERY_JSON):
            found.update(_HEX_RUN.findall(text))
        return frozenset(found)

    return read(connection, _SOURCE, body)


def store_sizes(connection: sqlite3.Connection, path: Path) -> SizeReport | Failure:
    """Report the sizes of the store's file, its log, its tables and largest sessions.

    It reads the whole events table, which takes seconds on a file of hundreds
    of megabytes.
    """

    def body(c: sqlite3.Connection) -> SizeReport:
        (page_bytes,) = c.execute("PRAGMA page_size").fetchone()
        (pages,) = c.execute("PRAGMA page_count").fetchone()
        (free_pages,) = c.execute("PRAGMA freelist_count").fetchone()
        tables = tuple(
            TableSize(table, *c.execute(sql).fetchone())
            for table, sql in (
                ("events", _COUNT_EVENTS),
                ("effects", _COUNT_EFFECTS),
                ("inbox", _COUNT_INBOX),
            )
        )
        largest = tuple(SessionSize(*row) for row in c.execute(_LARGEST_SESSIONS))
        return SizeReport(
            file_bytes=_size(path),
            log_bytes=_size(path.with_name(path.name + "-wal")),
            page_bytes=page_bytes,
            pages=pages,
            free_pages=free_pages,
            tables=tables,
            largest_sessions=largest,
        )

    return read(connection, _SOURCE, body)


_COUNT_EVENTS = (
    "SELECT count(*), COALESCE(SUM(length(CAST(payload AS BLOB))), 0) FROM events"
)
_COUNT_EFFECTS = (
    "SELECT count(*), COALESCE(SUM(length(CAST(action AS BLOB))"
    " + COALESCE(length(CAST(probe AS BLOB)), 0)"
    " + COALESCE(length(CAST(undo AS BLOB)), 0)"
    " + COALESCE(length(CAST(result AS BLOB)), 0)"
    " + COALESCE(length(CAST(resolution AS BLOB)), 0)), 0) FROM effects"
)
_COUNT_INBOX = (
    "SELECT count(*), COALESCE(SUM(length(CAST(inbound AS BLOB))), 0) FROM inbox"
)
_LARGEST_SESSIONS = (
    "SELECT session_id, count(*), SUM(length(CAST(payload AS BLOB))) AS bytes"
    " FROM events GROUP BY session_id ORDER BY bytes DESC, session_id LIMIT 10"
)


def _refusal(c: sqlite3.Connection, session_id: str) -> Failure | None:
    if not is_id(session_id):
        return _invalid("The session id breaks the id rule.")
    mark = c.execute(
        "SELECT type FROM events WHERE session_id = ? AND type IN (?, ?)"
        " ORDER BY seq DESC LIMIT 1",
        (session_id, RUN_STARTED, RUN_END),
    ).fetchone()
    if mark is not None and mark[0] == RUN_STARTED:
        return _refused(f"The session {session_id} has a run that did not end.")
    (open_effects,) = c.execute(
        "SELECT count(*) FROM effects WHERE session_id = ?"
        " AND state IN ('intended', 'unknown')",
        (session_id,),
    ).fetchone()
    if open_effects:
        return _refused(f"The session {session_id} has effects not yet settled.")
    return None


def _session_texts(c: sqlite3.Connection, session_id: str) -> _SessionTexts | Failure:
    texts: dict[str, str] = {}
    counts: dict[str, int] = {}
    handles: dict[str, BlobHandle] = {}
    last_seq = 0
    for name, sql in _SESSION_ROWS.items():
        cursor = c.execute(sql, (session_id,))
        columns = [description[0] for description in cursor.description]
        try:
            rows = [_plain_row(columns, row) for row in cursor.fetchall()]
        except ValueError:
            return damaged(f"A row for {name} holds JSON that does not read.", _SOURCE)
        texts[name] = "".join(saved_json(row) + "\n" for row in rows)
        counts[name] = len(rows)
        for row in rows:
            handles.update((h.sha256, h) for h in _handles_in(row))
        if name == "events.jsonl" and rows:
            last_seq = rows[-1]["seq"]
    return _SessionTexts(
        texts=texts,
        counts=counts,
        last_seq=last_seq,
        handles=tuple(handles[sha256] for sha256 in sorted(handles)),
        named=frozenset(_HEX_RUN.findall("".join(texts.values()))),
    )


def _plain_row(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    plain = dict(zip(columns, row, strict=True))
    for column in _JSON_COLUMNS.intersection(columns):
        if plain[column] is not None:
            plain[column] = read_saved_json(plain[column])
    return plain


def _handles_in(value: object) -> Iterator[BlobHandle]:
    # A blob is pointed at by a saved BlobHandle. Any other hex text, such as
    # an event id, names no blob and is not copied.
    if isinstance(value, dict):
        fields = cast(dict[str, Any], value)
        if _is_handle(fields):
            yield BlobHandle(fields["sha256"], fields["size"], fields["media_type"])
        for item in fields.values():
            yield from _handles_in(item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from _handles_in(item)


def _is_handle(fields: dict[str, Any]) -> bool:
    sha256, size = fields.get("sha256"), fields.get("size")
    return (
        isinstance(sha256, str)
        and is_hash(sha256)
        and type(size) is int
        and isinstance(fields.get("media_type"), str)
    )


async def _blobs_named_by_hash_alone(
    blobs: BlobStore, rows: _SessionTexts
) -> tuple[str, ...] | Failure:
    # The sweep keeps a blob for any 64 hex characters in a row, so a blob a
    # row names without its handle would be swept once the row is deleted.
    # An export copies through a handle, for its size check, so it cannot
    # take that blob, and must stop instead. Most such names, such as event
    # ids, name no blob at all.
    copied = {handle.sha256 for handle in rows.handles}
    found: list[str] = []
    for sha256 in sorted(rows.named - copied):
        there = await blobs.exists(BlobHandle(sha256, 0, "application/octet-stream"))
        if isinstance(there, Failure):
            return there
        if there:
            found.append(sha256)
    return tuple(found)


async def _write_export(
    blobs: BlobStore, folder: Path, rows: _SessionTexts
) -> tuple[str, ...] | Failure:
    try:
        folder.mkdir(mode=0o700, parents=True)
        (folder / "blobs").mkdir(mode=0o700)
        for name, text in rows.texts.items():
            write_flushed(folder / name, text.encode("utf-8"))
    except OSError as error:
        return _os_failure(error)
    copied: list[str] = []
    for handle in rows.handles:
        data = await blobs.get(handle)
        if isinstance(data, Failure):
            return data
        try:
            write_flushed(folder / "blobs" / handle.sha256, data)
        except OSError as error:
            return _os_failure(error)
        copied.append(handle.sha256)
    try:
        flush_folder(folder / "blobs")
    except OSError as error:
        return _os_failure(error)
    return tuple(copied)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0


def _invalid(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=_SOURCE)


def _refused(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)


def _os_failure(error: OSError) -> Failure:
    full = is_full(error)
    return Failure(
        kind="unavailable" if full else "fatal",
        effect="none",
        message="The drive is full." if full else "The export could not be written.",
        source=_SOURCE,
        detail={"os_error": error.strerror or str(error)},
    )
