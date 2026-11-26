"""Opens the store's SQLite file, applies its settings and creates its layout."""

import contextlib
import os
import sqlite3
import time
from pathlib import Path

from lamplighter.adapters.sqlite.errors import failure_for
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import Failure

# The letters LAMP read as one number, so a file of another program is never
# mistaken for a store.
APPLICATION_ID = 1279348048
LAYOUT_VERSION = 1

# SQLite's WAL page describes a bug, fixed in 3.51.3, that can damage a file
# when two connections write or checkpoint at the same instant. A retention
# command is a second connection, so this is the oldest version accepted.
OLDEST_SQLITE = (3, 51, 3)

_SOURCE = "store/sqlite"

_WAL_RETRY_S = 0.02

LAYOUT = (
    """CREATE TABLE events (
  pos          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT    NOT NULL,
  seq          INTEGER NOT NULL CHECK (seq >= 1),
  type         TEXT    NOT NULL,
  v            INTEGER NOT NULL CHECK (v >= 1),
  event_id     TEXT    NOT NULL,
  run_id       TEXT,
  step         INTEGER,
  at           TEXT    NOT NULL,
  duration_ms  INTEGER,
  cause        TEXT,
  payload      TEXT    NOT NULL,
  UNIQUE (session_id, seq),
  UNIQUE (event_id)
) STRICT""",
    # Both names are fixed for good, so a partial index on them is safe.
    """CREATE INDEX events_run_marks ON events (session_id, seq, type)
  WHERE type IN ('run_started', 'run_end')""",
    # The six states are not listed in a CHECK, because SQLite cannot change a
    # CHECK without rebuilding the table. The adapter writes only those six.
    """CREATE TABLE effects (
  effect_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  effect_key   TEXT    NOT NULL UNIQUE,
  v            INTEGER NOT NULL CHECK (v >= 1),
  session_id   TEXT    NOT NULL,
  state        TEXT    NOT NULL,
  action       TEXT    NOT NULL,
  probe        TEXT,
  undo         TEXT,
  result       TEXT,
  resolution   TEXT,
  begun_at     TEXT    NOT NULL,
  finished_at  TEXT,
  resolved_at  TEXT
) STRICT""",
    """CREATE INDEX effects_unresolved ON effects (effect_id)
  WHERE state IN ('intended', 'unknown')""",
    """CREATE TABLE inbox (
  inbox_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  channel            TEXT    NOT NULL,
  native_id          TEXT    NOT NULL,
  inbound            TEXT    NOT NULL,
  routed_session_id  TEXT,
  routed_seq         INTEGER,
  UNIQUE (channel, native_id)
) STRICT""",
    """CREATE INDEX inbox_pending ON inbox (inbox_id)
  WHERE routed_session_id IS NULL""",
)

# Each setting, the value it is set to, and what reading it back must give.
# Only journal_mode is kept in the file. The others belong to the connection,
# so they are set on every open, here and nowhere else.
_SETTINGS = (
    ("synchronous", "FULL", 2),
    ("fullfsync", "ON", 1),
    ("secure_delete", "ON", 1),
)


def open_database(path: Path, timeout_s: float) -> sqlite3.Connection | Failure:
    """Open the store at `path`, creating it if the file is missing.

    `timeout_s` is how long a write waits for another process that holds the
    write lock. A file of another program is refused before anything is
    written to it. The connection may only be used on the thread that opened
    it, and every adapter shares it.
    """
    if sqlite3.sqlite_version_info < OLDEST_SQLITE:
        return Failure(
            kind="needs_user",
            effect="none",
            message=f"SQLite {sqlite3.sqlite_version} is older than 3.51.3.",
            source=_SOURCE,
        )
    try:
        _create_empty(path)
    except OSError as error:
        return _os_failure(error)
    try:
        connection = sqlite3.connect(path, timeout=timeout_s, autocommit=True)
    except sqlite3.Error as error:
        return _sqlite_failure(error)
    try:
        refused = _prepare(connection, timeout_s)
    except BaseException as error:
        connection.close()
        if isinstance(error, sqlite3.Error):
            return _sqlite_failure(error)
        raise
    if refused is not None:
        connection.close()
        return refused
    return connection


def _create_empty(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Made here with mode 0600, so SQLite gives its two side files the same.
    # Another process may make it first, which is fine.
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def _prepare(connection: sqlite3.Connection, timeout_s: float) -> Failure | None:
    # Who owns the file is read before any setting is applied, because
    # setting journal_mode writes to the file. The three values are read in
    # one transaction, or a store being made by another process at the same
    # moment could show its tables and not yet its id.
    identity = read(connection, _SOURCE, _identity)
    if isinstance(identity, Failure):
        return identity
    refused = _refusal(*identity)
    if refused is not None:
        return refused
    refused = _switch_to_wal(connection, timeout_s)
    if refused is not None:
        return refused
    for name, value, expected in _SETTINGS:
        connection.execute(f"PRAGMA {name} = {value}")
        (found,) = connection.execute(f"PRAGMA {name}").fetchone()
        if found != expected:
            return _not_taken(name, value, found)
    is_new = identity[2] == 0
    if is_new:
        created = write(connection, _SOURCE, _create_layout)
        if isinstance(created, Failure):
            return created
    # Clears the write-ahead log, so rows that a killed retention command
    # deleted do not stay readable there past this start. A busy answer means
    # another process is at work, and is not an error.
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return None


def _switch_to_wal(connection: sqlite3.Connection, timeout_s: float) -> Failure | None:
    # SQLite does not wait for the lock this switch needs: it answers "busy"
    # at once while another connection has the file open. That only happens
    # when two processes make a new store at the same moment, because the
    # mode is kept in the file and every later open finds it set. So the
    # switch is tried again for about as long as a write would wait.
    tries_left = max(1, round(timeout_s / _WAL_RETRY_S))
    while True:
        try:
            (mode,) = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            break
        except sqlite3.OperationalError as error:
            tries_left -= 1
            if error.sqlite_errorcode & 0xFF != sqlite3.SQLITE_BUSY or not tries_left:
                raise
            time.sleep(_WAL_RETRY_S)
    return None if mode == "wal" else _not_taken("journal_mode", "WAL", mode)


def _not_taken(name: str, value: str, found: object) -> Failure:
    return Failure(
        kind="fatal",
        effect="none",
        message=f"SQLite did not take the setting {name} = {value}.",
        source=_SOURCE,
        detail={"found": str(found)},
    )


def _identity(connection: sqlite3.Connection) -> tuple[int, int, int]:
    (application_id,) = connection.execute("PRAGMA application_id").fetchone()
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    return application_id, version, _table_count(connection)


def _refusal(application_id: int, version: int, tables: int) -> Failure | None:
    if (application_id, version, tables) == (0, 0, 0):
        return None
    if application_id == APPLICATION_ID and version == LAYOUT_VERSION:
        return None
    if application_id == APPLICATION_ID and version > LAYOUT_VERSION:
        return Failure(
            kind="needs_user",
            effect="none",
            message=f"The store was written by newer code, at layout {version}.",
            source=_SOURCE,
        )
    return Failure(
        kind="fatal",
        effect="none",
        message="The file is not a Lamplighter store. It was left untouched.",
        source=_SOURCE,
        detail={"application_id": application_id, "user_version": version},
    )


def _create_layout(connection: sqlite3.Connection) -> bool:
    # Read again under the write lock: another process may have just made it.
    if _table_count(connection) > 0:
        return False
    for statement in LAYOUT:
        connection.execute(statement)
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version = {LAYOUT_VERSION}")
    return True


def _table_count(connection: sqlite3.Connection) -> int:
    (count,) = connection.execute(
        "SELECT count(*) FROM sqlite_schema WHERE type = 'table'"
    ).fetchone()
    return count


def _sqlite_failure(error: sqlite3.Error) -> Failure:
    failure = failure_for(error, _SOURCE, "none")
    if failure is None:
        raise error
    return failure


def _os_failure(error: OSError) -> Failure:
    return Failure(
        kind="fatal",
        effect="none",
        message="The store's folder or file could not be made.",
        source=_SOURCE,
        detail={"os_error": error.strerror or str(error)},
    )
