"""The outbox on the store's SQLite file."""

import dataclasses
import sqlite3

from lamplighter.adapters.sqlite.errors import damaged
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import (
    Clock,
    DeliveryReceipt,
    Failure,
    Outbound,
    as_saved,
    check_enqueue,
    check_receipt,
    from_stored,
    read_saved_json,
    saved_json,
    unknown_outbound,
    utc_text,
)

_SOURCE = "Outbox/sqlite"


class SqliteOutbox:
    """Keeps messages to send in the store's outbox table. Satisfies Outbox.

    It shares the connection that `open_database` gave, and runs each call as
    one transaction on the caller's thread, so calls never interleave. The
    lasting mark that a row may have gone out is the `maybe_duplicate`
    column, and nothing ever sets it back to 0.
    """

    def __init__(
        self, connection: sqlite3.Connection, clock: Clock, max_record_bytes: int
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._max_record_bytes = max_record_bytes

    async def enqueue(self, out: Outbound) -> None | Failure:
        """Save a message to send, or find the same one already saved."""
        refused = check_enqueue(out, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        text = saved_json(out)
        at = utc_text(self._clock.now())

        def body(connection: sqlite3.Connection) -> None | Failure:
            row = connection.execute(
                "SELECT outbound FROM outbox WHERE out_id = ?", (out.out_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO outbox (out_id, session_id, v, state, outbound,"
                    " enqueued_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                    (out.out_id, out.session_id, out.v, text, at),
                )
                return None
            if _read_outbound(row[0]) == as_saved(Outbound, out):
                return None
            return _conflict("Another message is saved under this out_id.")

        return write(self._connection, _SOURCE, body)

    async def due(self) -> tuple[Outbound, ...] | Failure:
        """Give every message not yet sent, oldest first."""
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(
                "SELECT outbox_id, state, maybe_duplicate, outbound FROM outbox"
                " WHERE state IN ('pending', 'sending') ORDER BY outbox_id"
            ).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        found: list[Outbound] = []
        for outbox_id, state, mark, text in rows:
            try:
                out = _read_outbound(text)
            except ValueError:
                return damaged(f"The outbox row {outbox_id} does not fit.", _SOURCE)
            maybe = state == "sending" or bool(mark)
            found.append(dataclasses.replace(out, maybe_duplicate=maybe))
        return tuple(found)

    async def start_send(self, out_id: str) -> None | Failure:
        """Save that a send of this row is about to begin."""

        def body(connection: sqlite3.Connection) -> None | Failure:
            state = _state(connection, out_id)
            if state is None:
                return unknown_outbound(out_id, _SOURCE)
            if state == "sent":
                return _conflict("The message was already sent.")
            # A row found in "sending" was being sent when a process died.
            connection.execute(
                "UPDATE outbox SET maybe_duplicate = CASE WHEN state = 'sending'"
                " THEN 1 ELSE maybe_duplicate END, state = 'sending'"
                " WHERE out_id = ?",
                (out_id,),
            )
            return None

        return write(self._connection, _SOURCE, body)

    async def mark_sent(self, out_id: str, receipt: DeliveryReceipt) -> None | Failure:
        """Save that the row was sent, with the channel's receipt."""
        refused = check_receipt(receipt, _SOURCE)
        if refused is not None:
            return refused
        text = saved_json(receipt)
        at = utc_text(self._clock.now())

        def body(connection: sqlite3.Connection) -> None | Failure:
            row = connection.execute(
                "SELECT state, receipt FROM outbox WHERE out_id = ?", (out_id,)
            ).fetchone()
            if row is None:
                return unknown_outbound(out_id, _SOURCE)
            state, saved = row
            if state == "sent":
                if _read_receipt(saved) == as_saved(DeliveryReceipt, receipt):
                    return None
                return _conflict("The message was already sent with another receipt.")
            connection.execute(
                "UPDATE outbox SET state = 'sent', receipt = ?, sent_at = ?"
                " WHERE out_id = ?",
                (text, at, out_id),
            )
            return None

        return write(self._connection, _SOURCE, body)

    async def mark_failed(self, out_id: str, failure: Failure) -> None | Failure:
        """Save that a send failed, so the row is tried again later."""
        text = saved_json(failure)
        mark = 0 if failure.effect == "none" else 1

        def body(connection: sqlite3.Connection) -> None | Failure:
            state = _state(connection, out_id)
            if state is None:
                return unknown_outbound(out_id, _SOURCE)
            if state == "sent":
                return _conflict("The message was already sent.")
            connection.execute(
                "UPDATE outbox SET state = 'pending', last_failure = ?,"
                " maybe_duplicate = MAX(maybe_duplicate, ?) WHERE out_id = ?",
                (text, mark, out_id),
            )
            return None

        return write(self._connection, _SOURCE, body)


def _state(connection: sqlite3.Connection, out_id: str) -> str | None:
    row = connection.execute(
        "SELECT state FROM outbox WHERE out_id = ?", (out_id,)
    ).fetchone()
    return None if row is None else row[0]


def _read_outbound(text: str) -> Outbound:
    return from_stored(Outbound, read_saved_json(text))


def _read_receipt(text: str) -> DeliveryReceipt:
    return from_stored(DeliveryReceipt, read_saved_json(text))


def _conflict(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)
