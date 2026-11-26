"""Code that talks to the outside world. Each adapter implements one port."""

from lamplighter.adapters.counting_ids import CountingIds
from lamplighter.adapters.file_blob_store import BlobTotals, FileBlobStore
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
from lamplighter.adapters.stub_tools import StubTools
from lamplighter.adapters.system_clock import SystemClock
from lamplighter.adapters.system_ids import SystemIds
from lamplighter.adapters.terminal import TerminalChannel

__all__ = [
    "CountingIds",
    "ScriptedChannel",
    "ScriptedTool",
    "ScriptedTools",
    "StubTools",
    "SystemIds",
    "TerminalChannel",
    "ToolCallNote",
    "BlobTotals",
    "FileBlobStore",
    "RecordingModel",
    "ReplayedModel",
    "ScriptedBreak",
    "ScriptedModel",
    "ScriptedReply",
    "SystemClock",
]
