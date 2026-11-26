"""The inbox on the store's SQLite file."""

import sqlite3

from lamplighter.adapters.sqlite.errors import damaged
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import (
    Accepted,
    Failure,
    Inbound,
    InboxEntry,
    check_accept,
    check_mark_routed,
    from_stored,
    read_saved_json,
    saved_json,
    unknown_inbox_row,
)

_SOURCE = "Inbox/sqlite"


class SqliteInbox:
    """Keeps messages in the store's inbox table. Satisfies Inbox.

    It shares the connection that `open_database` gave, and runs each call as
    one transaction on the caller's thread, so calls never interleave.
    """

    def __init__(self, connection: sqlite3.Connection, max_record_bytes: int) -> None:
        self._connection = connection
        self._max_record_bytes = max_record_bytes

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        """Save an arriving message, or find the copy already saved."""
        refused = check_accept(inbound, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        key = (inbound.channel, inbound.native_id)
        text = saved_json(inbound)

        def body(connection: sqlite3.Connection) -> Accepted:
            made = connection.execute(
                "INSERT INTO inbox (channel, native_id, inbound) VALUES (?, ?, ?)"
                " ON CONFLICT (channel, native_id) DO NOTHING RETURNING inbox_id",
                (*key, text),
            ).fetchall()
            if made:
                return Accepted(inbox_id=made[0][0], is_new=True)
            (inbox_id,) = connection.execute(
                "SELECT inbox_id FROM inbox WHERE channel = ? AND native_id = ?", key
            ).fetchone()
            return Accepted(inbox_id=inbox_id, is_new=False)

        return write(self._connection, _SOURCE, body)

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        """Give every message not yet routed, oldest first."""
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(
                "SELECT inbox_id, inbound FROM inbox"
                " WHERE routed_session_id IS NULL ORDER BY inbox_id"
            ).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        entries: list[InboxEntry] = []
        for inbox_id, text in rows:
            try:
                inbound = from_stored(Inbound, read_saved_json(text))
            except ValueError:
                return damaged(f"The inbox row {inbox_id} does not fit.", _SOURCE)
            entries.append(InboxEntry(inbox_id=inbox_id, inbound=inbound))
        return tuple(entries)

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        """Save which session and seq a message was saved to as an event."""
        refused = check_mark_routed(session_id, seq, _SOURCE)
        if refused is not None:
            return refused

        def body(connection: sqlite3.Connection) -> None | Failure:
            changed = connection.execute(
                "UPDATE inbox SET routed_session_id = ?, routed_seq = ?"
                " WHERE inbox_id = ? AND routed_session_id IS NULL",
                (session_id, seq, inbox_id),
            ).rowcount
            if changed:
                return None
            row = connection.execute(
                "SELECT routed_session_id, routed_seq FROM inbox WHERE inbox_id = ?",
                (inbox_id,),
            ).fetchone()
            if row is None:
                return unknown_inbox_row(inbox_id, _SOURCE)
            if tuple(row) == (session_id, seq):
                return None
            return Failure(
                kind="conflict",
                effect="none",
                message="The message was already routed somewhere else.",
                source=_SOURCE,
            )

        return write(self._connection, _SOURCE, body)
