"""The ports one call of the loop uses, handed in together."""

from collections.abc import Mapping
from dataclasses import dataclass

from lamplighter.core.observers import Observers
from lamplighter.core.running_sessions import RunningSessions
from lamplighter.ports import (
    BlobStore,
    Clock,
    EffectLedger,
    EventLog,
    HealthCheck,
    IdSource,
    Inbox,
    ModelClient,
    Outbox,
    ToolProvider,
)


@dataclass(frozen=True, slots=True)
class Deps:
    """Everything outside the loop that the loop reads, writes or calls.

    `models` holds a client for each model entry of the settings, by its
    name, and `health` the health check of each that runs in a separate
    process on this Mac. `ledger` records every change before and after its
    tool runs. `blobs` holds the whole text of a result that was cut. `observers` and
    `running` are not ports. They touch nothing outside the process, and
    `running` is shared by the whole of it.
    """

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    models: Mapping[str, ModelClient]
    health: Mapping[str, HealthCheck]
    tools: ToolProvider
    ledger: EffectLedger
    blobs: BlobStore
    clock: Clock
    ids: IdSource
    observers: Observers
    running: RunningSessions
