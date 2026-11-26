"""Turns what SQLite reports as wrong into this repo's failure values."""

import sqlite3

from lamplighter.ports import Failure, FailureEffect, FailureKind

# Extended codes, for the one kind of constraint a caller can trigger: a key
# that is already saved. Any other constraint failing is a bug in this code.
_ALREADY_SAVED = frozenset(
    {sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY}
)

# Primary codes. The low byte of an extended code is its primary code.
_KIND_BY_CODE: dict[int, FailureKind] = {
    sqlite3.SQLITE_BUSY: "unavailable",
    sqlite3.SQLITE_LOCKED: "unavailable",
    sqlite3.SQLITE_FULL: "unavailable",
    sqlite3.SQLITE_IOERR: "fatal",
    sqlite3.SQLITE_CORRUPT: "fatal",
    sqlite3.SQLITE_NOTADB: "fatal",
    sqlite3.SQLITE_CANTOPEN: "fatal",
    sqlite3.SQLITE_READONLY: "fatal",
}

_MESSAGE_BY_CODE = {
    sqlite3.SQLITE_BUSY: "Another process held the store's write lock for too long.",
    sqlite3.SQLITE_LOCKED: "Another process held the store's write lock for too long.",
    sqlite3.SQLITE_FULL: "The drive that holds the store is full.",
    sqlite3.SQLITE_IOERR: "The drive reported an error while the store was in use.",
    sqlite3.SQLITE_CORRUPT: "The store's file is damaged.",
    sqlite3.SQLITE_NOTADB: "The file is not a SQLite database.",
    sqlite3.SQLITE_CANTOPEN: "The store's file could not be opened.",
    sqlite3.SQLITE_READONLY: "The store's file cannot be written.",
}


def failure_for(
    error: sqlite3.Error, source: str, fatal_effect: FailureEffect
) -> Failure | None:
    """Give the Failure for a SQLite error a caller can meet, or None for a bug.

    `fatal_effect` is the effect of a fatal error. It is "unknown" when the
    error came from a commit, because the drive may have kept the change.
    Every other kind means nothing was written.
    """
    code = error.sqlite_errorcode
    detail = {"sqlite_error": error.sqlite_errorname, "sqlite_message": str(error)}
    if code in _ALREADY_SAVED:
        return Failure(
            kind="conflict",
            effect="none",
            message="A row with this key is already saved.",
            source=source,
            detail=detail,
        )
    kind = _KIND_BY_CODE.get(code & 0xFF)
    if kind is None:
        return None
    return Failure(
        kind=kind,
        effect=fatal_effect if kind == "fatal" else "none",
        message=_MESSAGE_BY_CODE[code & 0xFF],
        source=source,
        detail=detail,
    )


def damaged(message: str, source: str) -> Failure:
    """Build the failure for a saved row that does not fit its record."""
    return Failure(kind="fatal", effect="none", message=message, source=source)
