"""The ports one call of the loop uses, handed in together."""

from dataclasses import dataclass

from lamplighter.core.observers import Observers
from lamplighter.core.running_sessions import RunningSessions
from lamplighter.ports import (
    Clock,
    EventLog,
    IdSource,
    Inbox,
    ModelClient,
    Outbox,
    ToolProvider,
)


@dataclass(frozen=True, slots=True)
class Deps:
    """Everything outside the loop that the loop reads, writes or calls.

    `observers` and `running` are not ports. They touch nothing outside the
    process, and `running` is shared by the whole of it.
    """

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    model: ModelClient
    tools: ToolProvider
    clock: Clock
    ids: IdSource
    observers: Observers
    running: RunningSessions
