"""Code that talks to the outside world. Each adapter implements one port."""

from lamplighter.adapters.recorded_model import RecordingModel, ReplayedModel
from lamplighter.adapters.scripted_model import (
    ScriptedBreak,
    ScriptedModel,
    ScriptedReply,
)
from lamplighter.adapters.system_clock import SystemClock

__all__ = [
    "RecordingModel",
    "ReplayedModel",
    "ScriptedBreak",
    "ScriptedModel",
    "ScriptedReply",
    "SystemClock",
]
