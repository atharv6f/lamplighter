"""An event log kept in memory, for tests. It refuses exactly what the real one does."""

import dataclasses
from collections.abc import Sequence
from typing import cast

from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    Event,
    Failure,
    JsonObject,
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

_SOURCE = "EventLog/in_memory"


class InMemoryEventLog:
    """Keeps every event in lists. Satisfies EventLog.

    It cannot fail with `unavailable` or `fatal`, because nothing it holds can
    be out of reach or damaged.
    """

    def __init__(self, upcasters: Upcasters, max_record_bytes: int) -> None:
        self._upcasters = upcasters
        self._max_record_bytes = max_record_bytes
        self._by_session: dict[str, list[Event]] = {}
        self._in_order: list[Event] = []
        self._ids: set[str] = set()
        self._last_pos = 0

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        """Save a batch at the end of a session, and give the new last place."""
        refused = check_append(
            session_id, events, expected_seq, self._max_record_bytes, _SOURCE
        )
        if refused is not None:
            return refused
        saved = self._by_session.get(session_id, [])
        if len(saved) != expected_seq:
            return _conflict("The session's last seq is not expected_seq.")
        batch = [
            _saved(
                session_id, expected_seq + 1 + place, self._last_pos + 1 + place, new
            )
            for place, new in enumerate(events)
        ]
        ids = [event.event_id for event in batch]
        if len(set(ids)) != len(ids) or self._ids.intersection(ids):
            return _conflict("An event id in the batch is already in the log.")
        self._by_session.setdefault(session_id, []).extend(batch)
        self._in_order.extend(batch)
        self._ids.update(ids)
        self._last_pos += len(batch)
        return expected_seq + len(batch)

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        """Give every event of a session after `after_seq`, in order."""
        refused = check_read(session_id, after_seq, _SOURCE)
        if refused is not None:
            return refused
        saved = self._by_session.get(session_id, [])
        return tuple(self._newest(event) for event in saved[after_seq:])

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        """Give one summary per session in that state, or all, by session id."""
        infos = (_info(saved) for _, saved in sorted(self._by_session.items()))
        return tuple(info for info in infos if state is None or info.state == state)

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        """Give up to `limit` events from all sessions after `after_pos`."""
        refused = check_tail(after_pos, limit, _SOURCE)
        if refused is not None:
            return refused
        later = [event for event in self._in_order if event.pos > after_pos]
        return tuple(self._newest(event) for event in later[:limit])

    def _newest(self, event: Event) -> Event:
        v, payload = upcast(event.type, event.v, event.payload, self._upcasters)
        return dataclasses.replace(event, v=v, payload=payload)


def _saved(session_id: str, seq: int, pos: int, new: NewEvent) -> Event:
    # The payload goes through its saved text, so a reader gets the same plain
    # values from this log as from one on the drive.
    payload = cast(JsonObject, read_saved_json(saved_json(new.payload)))
    return Event(
        session_id=session_id,
        seq=seq,
        pos=pos,
        event_id=new.event_id or event_id_for(session_id, seq),
        type=new.type,
        v=new.v,
        payload=payload,
        at=new.at,
        run_id=new.run_id,
        step=new.step,
        duration_ms=new.duration_ms,
        cause=new.cause,
    )


def _info(saved: list[Event]) -> SessionInfo:
    marks = [event.type for event in saved if event.type in (RUN_STARTED, RUN_END)]
    last = saved[-1]
    return SessionInfo(
        session_id=last.session_id,
        last_seq=last.seq,
        last_at=last.at,
        state="unfinished" if marks and marks[-1] == RUN_STARTED else "idle",
    )


def _conflict(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)
