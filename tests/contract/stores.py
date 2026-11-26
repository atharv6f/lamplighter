"""Builds each kind of store the contract tests run against.

Every kind is built on a fresh folder with the same small size limits, so a
test of a limit stays quick.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.in_memory import InMemoryEffectLedger, InMemoryEventLog
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    open_database,
)
from lamplighter.ports import EffectLedger, EventLog, Failure, Upcasters
from tests.fake_clock import FakeClock

MAX_RECORD_BYTES = 2_000


class InMemoryStores:
    """Stores that live in memory."""

    def __init__(self, folder: Path, clock: FakeClock) -> None:
        del folder
        self.clock = clock

    def log(self, upcasters: Upcasters) -> EventLog:
        return InMemoryEventLog(upcasters, MAX_RECORD_BYTES)

    def ledger(self) -> EffectLedger:
        return InMemoryEffectLedger(self.clock, MAX_RECORD_BYTES)

    def close(self) -> None:
        pass


class SqliteStores:
    """Stores on one SQLite file in the test's folder, sharing one connection."""

    def __init__(self, folder: Path, clock: FakeClock) -> None:
        self.clock = clock
        connection = open_database(folder / "state.db", timeout_s=0.2)
        assert not isinstance(connection, Failure), connection
        self.connection: sqlite3.Connection = connection

    def log(self, upcasters: Upcasters) -> EventLog:
        return SqliteEventLog(self.connection, upcasters, MAX_RECORD_BYTES)

    def ledger(self) -> EffectLedger:
        return SqliteEffectLedger(self.connection, self.clock, MAX_RECORD_BYTES)

    def close(self) -> None:
        self.connection.close()


type Stores = InMemoryStores | SqliteStores


@dataclass(frozen=True)
class StoreKind:
    """One kind of store, and how to build it on a folder."""

    make: Callable[[Path, FakeClock], Stores]


KINDS = {
    "in_memory": StoreKind(make=InMemoryStores),
    "sqlite": StoreKind(make=SqliteStores),
}
