"""Code that talks to the outside world. Each adapter implements one port."""

from lamplighter.adapters.counting_ids import CountingIds
from lamplighter.adapters.fake_executor import FakeExecutor
from lamplighter.adapters.file_blob_store import BlobTotals, FileBlobStore
from lamplighter.adapters.file_ledger import FileLedger
from lamplighter.adapters.file_trace_cursor import FileTraceCursor
from lamplighter.adapters.loopback_address import local_address
from lamplighter.adapters.made_up_world import MadeUpWorld, WorldTools
from lamplighter.adapters.recorded_model import RecordingModel, ReplayedModel
from lamplighter.adapters.scripted_channel import ScriptedChannel
from lamplighter.adapters.scripted_model import (
    ScriptedBreak,
    ScriptedModel,
    ScriptedReply,
)
from lamplighter.adapters.scripted_tools import (
    ScriptedTool,
    ScriptedTools,
    ToolCallNote,
)
from lamplighter.adapters.system_clock import SystemClock
from lamplighter.adapters.system_ids import SystemIds
from lamplighter.adapters.terminal import TerminalChannel

__all__ = [
    "CountingIds",
    "FakeExecutor",
    "FileLedger",
    "MadeUpWorld",
    "WorldTools",
    "ScriptedChannel",
    "ScriptedTool",
    "ScriptedTools",
    "SystemIds",
    "TerminalChannel",
    "ToolCallNote",
    "BlobTotals",
    "FileBlobStore",
    "FileTraceCursor",
    "RecordingModel",
    "ReplayedModel",
    "ScriptedBreak",
    "ScriptedModel",
    "ScriptedReply",
    "SystemClock",
    "local_address",
]
