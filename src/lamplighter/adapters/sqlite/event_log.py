"""The event log on the store's SQLite file."""

import dataclasses
import sqlite3
from collections.abc import Sequence
from typing import Any

from lamplighter.adapters.sqlite.errors import damaged
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import (
    RUN_STARTED,
    Event,
    Failure,
    NewEvent,
    SessionInfo,
    SessionState,
    Upcasters,
    check_append,
    check_read,
    check_tail,
    event_id_for,
    read_saved_json,
    saved_json,
    upcast,
)

_SOURCE = "EventLog/sqlite"

_COLUMNS = (
    "session_id, seq, pos, event_id, type, v, payload, at,"
    " run_id, step, duration_ms, cause"
)

_INSERT = (
    "INSERT INTO events (session_id, seq, event_id, type, v, payload, at,"
    " run_id, step, duration_ms, cause) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# The last event of each session, and the last run mark of each session.
# When a query has one MAX(), SQLite takes the other plain columns from the
# row that holds the maximum.
_SESSIONS = """
SELECT last.session_id, last.seq, last.at, mark.type
  FROM (SELECT session_id, MAX(seq) AS seq, at FROM events
         GROUP BY session_id) AS last
  LEFT JOIN (SELECT session_id, MAX(seq) AS seq, type FROM events
              WHERE type IN ('run_started', 'run_end')
              GROUP BY session_id) AS mark
    ON mark.session_id = last.session_id
 ORDER BY last.session_id
"""


class SqliteEventLog:
    """Keeps events in the store's events table. Satisfies EventLog.

    It shares the connection that `open_database` gave, and runs each call as
    one transaction on the caller's thread, so calls never interleave.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        upcasters: Upcasters,
        max_record_bytes: int,
    ) -> None:
        self._connection = connection
        self._upcasters = upcasters
        self._max_record_bytes = max_record_bytes

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        """Save a batch at the end of a session, and give the new last place."""
        refused = check_append(
            session_id, events, expected_seq, self._max_record_bytes, _SOURCE
        )
        if refused is not None:
            return refused
        rows = [
            _row(session_id, expected_seq + 1 + place, event)
            for place, event in enumerate(events)
        ]

        def body(connection: sqlite3.Connection) -> int | Failure:
            (last,) = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if last != expected_seq:
                return Failure(
                    kind="conflict",
                    effect="none",
                    message="The session's last seq is not expected_seq.",
                    source=_SOURCE,
                    detail={"last_seq": last},
                )
            connection.executemany(_INSERT, rows)
            return expected_seq + len(rows)

        return write(self._connection, _SOURCE, body)

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        """Give every event of a session after `after_seq`, in order."""
        refused = check_read(session_id, after_seq, _SOURCE)
        if refused is not None:
            return refused
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(
                f"SELECT {_COLUMNS} FROM events"
                " WHERE session_id = ? AND seq > ? ORDER BY seq",
                (session_id, after_seq),
            ).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        events = self._events(rows)
        if isinstance(events, Failure):
            return events
        for expected, event in enumerate(events, start=after_seq + 1):
            if event.seq != expected:
                return damaged(
                    f"Session {session_id} has a gap in seq before {event.seq}.",
                    _SOURCE,
                )
        return events

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        """Give one summary per session in that state, or all, by session id."""
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(_SESSIONS).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        infos = (
            SessionInfo(
                session_id=session_id,
                last_seq=last_seq,
                last_at=last_at,
                state="unfinished" if mark == RUN_STARTED else "idle",
            )
            for session_id, last_seq, last_at, mark in rows
        )
        return tuple(info for info in infos if state is None or info.state == state)

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        """Give up to `limit` events from all sessions after `after_pos`."""
        refused = check_tail(after_pos, limit, _SOURCE)
        if refused is not None:
            return refused
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(
                f"SELECT {_COLUMNS} FROM events WHERE pos > ? ORDER BY pos LIMIT ?",
                (after_pos, limit),
            ).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        return self._events(rows)

    def _events(self, rows: list[Any]) -> tuple[Event, ...] | Failure:
        events: list[Event] = []
        for row in rows:
            try:
                event = _event(row)
            except ValueError:
                return damaged(f"The event at pos {row[2]} does not fit.", _SOURCE)
            v, payload = upcast(event.type, event.v, event.payload, self._upcasters)
            events.append(dataclasses.replace(event, v=v, payload=payload))
        return tuple(events)


def _row(session_id: str, seq: int, event: NewEvent) -> tuple[object, ...]:
    return (
        session_id,
        seq,
        event.event_id or event_id_for(session_id, seq),
        event.type,
        event.v,
        saved_json(event.payload),
        event.at,
        event.run_id,
        event.step,
        event.duration_ms,
        event.cause,
    )


def _event(row: Any) -> Event:
    session_id, seq, pos, event_id, kind, v, payload, at, run_id, step, ms, cause = row
    plain = read_saved_json(payload)
    if not isinstance(plain, dict):
        raise ValueError("a payload is a JSON object")
    return Event(
        session_id=session_id,
        seq=seq,
        pos=pos,
        event_id=event_id,
        type=kind,
        v=v,
        payload=plain,
        at=at,
        run_id=run_id,
        step=step,
        duration_ms=ms,
        cause=cause,
    )
