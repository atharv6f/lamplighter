"""Whether a session is private, read from its events. Pure."""

from collections.abc import Sequence

from lamplighter.ports import RUN_STARTED, Event


def session_is_private(events: Sequence[Event]) -> bool:
    """Say whether any run of the session was started as private.

    This is the one reading of privacy for every caller. A session that
    was private once stays private: no later run can make it ordinary.
    """
    return any(
        event.type == RUN_STARTED and event.payload.get("private") is True
        for event in events
    )
