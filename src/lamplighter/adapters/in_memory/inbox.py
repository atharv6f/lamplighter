"""An inbox kept in memory, for tests. It refuses what the real one does."""

from dataclasses import dataclass

from lamplighter.ports import (
    Accepted,
    Failure,
    Inbound,
    InboxEntry,
    as_saved,
    check_accept,
    check_mark_routed,
    unknown_inbox_row,
)

_SOURCE = "Inbox/in_memory"


@dataclass
class _Row:
    inbound: Inbound
    routed: tuple[str, int] | None = None


class InMemoryInbox:
    """Keeps every message in a dict by its inbox id. Satisfies Inbox."""

    def __init__(self, max_record_bytes: int) -> None:
        self._max_record_bytes = max_record_bytes
        self._rows: dict[int, _Row] = {}
        self._id_by_key: dict[tuple[str, str], int] = {}

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        """Save an arriving message, or find the copy already saved."""
        refused = check_accept(inbound, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        key = (inbound.channel, inbound.native_id)
        if key in self._id_by_key:
            return Accepted(inbox_id=self._id_by_key[key], is_new=False)
        inbox_id = len(self._rows) + 1
        self._rows[inbox_id] = _Row(inbound=as_saved(Inbound, inbound))
        self._id_by_key[key] = inbox_id
        return Accepted(inbox_id=inbox_id, is_new=True)

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        """Give every message not yet routed, oldest first."""
        return tuple(
            InboxEntry(inbox_id=inbox_id, inbound=row.inbound)
            for inbox_id, row in sorted(self._rows.items())
            if row.routed is None
        )

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        """Save which session and seq a message was saved to as an event."""
        refused = check_mark_routed(session_id, seq, _SOURCE)
        if refused is not None:
            return refused
        row = self._rows.get(inbox_id)
        if row is None:
            return unknown_inbox_row(inbox_id, _SOURCE)
        if row.routed is None:
            row.routed = (session_id, seq)
            return None
        if row.routed == (session_id, seq):
            return None
        return Failure(
            kind="conflict",
            effect="none",
            message="The message was already routed somewhere else.",
            source=_SOURCE,
        )
