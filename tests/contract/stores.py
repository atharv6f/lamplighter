"""Builds each kind of store the contract tests run against.

Every kind is built on a fresh folder with the same small size limits, so a
test of a limit stays quick.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.in_memory import InMemoryEventLog
from lamplighter.ports import EventLog, Upcasters
from tests.fake_clock import FakeClock

MAX_RECORD_BYTES = 2_000


class InMemoryStores:
    """Stores that live in memory."""

    def __init__(self, folder: Path, clock: FakeClock) -> None:
        del folder, clock

    def log(self, upcasters: Upcasters) -> EventLog:
        return InMemoryEventLog(upcasters, MAX_RECORD_BYTES)


type Stores = InMemoryStores


@dataclass(frozen=True)
class StoreKind:
    """One kind of store, and how to build it on a folder."""

    make: Callable[[Path, FakeClock], Stores]


KINDS = {"in_memory": StoreKind(make=InMemoryStores)}
