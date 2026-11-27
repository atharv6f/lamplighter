"""The agent's own logic. Imports ports, never adapters."""

from lamplighter.core import harness_texts
from lamplighter.core.arguments_check import unsupported
from lamplighter.core.chat_session import Wake, drive
from lamplighter.core.config_id import config_id
from lamplighter.core.cost import Prices, cost_micro_dollars
from lamplighter.core.day_report import DayReport
from lamplighter.core.delivery import deliver_due
from lamplighter.core.event_types import (
    CANCELLED,
    LIMIT_HIT,
    MODEL_REQUEST,
    MODEL_RESPONSE,
    REPLY_READY,
    SESSION_STARTED,
    TOOL_INTENT,
    TOOL_RESULT,
)
from lamplighter.core.intake import run_intake, take
from lamplighter.core.observers import (
    ModelCallEnded,
    ModelCallStarted,
    Observers,
    ToolCallEnded,
    ToolCallStarted,
)
from lamplighter.core.projector_loop import pass_to_the_end, project_while_open
from lamplighter.core.recovery import UnfinishedSession, UnfinishedWork, unfinished_work
from lamplighter.core.reply import Reply, fold_reply
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_ending import TurnEnd
from lamplighter.core.run_spans import SPAN_LAYOUT
from lamplighter.core.run_spec import RunSettings, RunSpec
from lamplighter.core.run_turn import run_turn
from lamplighter.core.running_sessions import RunningSessions
from lamplighter.core.trace_projector import PassDone, TraceProjector
from lamplighter.core.transcript import rebuild_messages

__all__ = [
    "CANCELLED",
    "LIMIT_HIT",
    "MODEL_REQUEST",
    "REPLY_READY",
    "SESSION_STARTED",
    "SPAN_LAYOUT",
    "TOOL_INTENT",
    "MODEL_RESPONSE",
    "TOOL_RESULT",
    "DayReport",
    "Deps",
    "ModelCallEnded",
    "ModelCallStarted",
    "Observers",
    "PassDone",
    "Prices",
    "Reply",
    "RunSettings",
    "RunSpec",
    "RunningSessions",
    "ToolCallEnded",
    "ToolCallStarted",
    "TraceProjector",
    "TurnEnd",
    "Wake",
    "UnfinishedSession",
    "UnfinishedWork",
    "cost_micro_dollars",
    "deliver_due",
    "drive",
    "fold_reply",
    "harness_texts",
    "pass_to_the_end",
    "project_while_open",
    "rebuild_messages",
    "run_intake",
    "run_turn",
    "config_id",
    "take",
    "unfinished_work",
    "unsupported",
]
