"""The trace store Phoenix, on this Mac: its address rule and its sink."""

from lamplighter.adapters.phoenix.local_address import local_address
from lamplighter.adapters.phoenix.sink import PhoenixSink

__all__ = ["PhoenixSink", "local_address"]
