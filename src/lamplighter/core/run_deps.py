"""The ports one call of the loop uses, handed in together."""

from dataclasses import dataclass

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
    """Everything outside the loop that the loop reads, writes or calls."""

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    model: ModelClient
    tools: ToolProvider
    clock: Clock
    ids: IdSource
