"""Stores that keep everything in memory, for tests. Each satisfies one port."""

from lamplighter.adapters.in_memory.effect_ledger import InMemoryEffectLedger
from lamplighter.adapters.in_memory.event_log import InMemoryEventLog

__all__ = ["InMemoryEffectLedger", "InMemoryEventLog"]
