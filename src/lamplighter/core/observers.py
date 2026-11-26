"""Functions that watch the loop's model calls and tool calls, and change nothing."""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from lamplighter.core.interruptible import CutShort, StopSignal, interruptible
from lamplighter.core.reply import Reply
from lamplighter.ports import Clock, ModelRequest, ToolCallPart, ToolResultPart

_log = logging.getLogger(__name__)

# How long one observer may take before it is cancelled.
OBSERVER_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ModelCallStarted:
    """A model request is saved and about to be sent."""

    session_id: str
    run_id: str
    step: int
    request: ModelRequest


@dataclass(frozen=True, slots=True)
class ModelCallEnded:
    """A model reply is saved."""

    session_id: str
    run_id: str
    step: int
    reply: Reply
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool intent is saved and the tool is about to be called."""

    session_id: str
    run_id: str
    step: int
    call: ToolCallPart


@dataclass(frozen=True, slots=True)
class ToolCallEnded:
    """The result of a tool that was called is saved."""

    session_id: str
    run_id: str
    step: int
    call: ToolCallPart
    result: ToolResultPart
    duration_ms: int


type Observer[T] = Callable[[T], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class Observers:
    """Four lists of observers. Each list is called at one point of a call.

    `before_model` and `before_tool` are called after the event that opens
    the call is saved. `after_model` is called once a reply is saved, which
    a call cut short by a stop never has. `after_tool` is called once the
    result of a tool that was called is saved.
    """

    before_model: tuple[Observer[ModelCallStarted], ...] = ()
    after_model: tuple[Observer[ModelCallEnded], ...] = ()
    before_tool: tuple[Observer[ToolCallStarted], ...] = ()
    after_tool: tuple[Observer[ToolCallEnded], ...] = ()


async def notify[T](observers: Sequence[Observer[T]], record: T, clock: Clock) -> None:
    """Call each observer in turn with the record, each for at most a second.

    An observer that raises or takes too long is logged, and the loop goes
    on as if it had none: an observer can change nothing the loop does.
    """
    for observer in observers:
        never = StopSignal(known=lambda: False, wait=asyncio.Event().wait)
        try:
            raced = await interruptible(
                observer(record), never, clock, OBSERVER_SECONDS, bound_s=0
            )
        except Exception:
            # An observer's own fault must not reach the run.
            _log.warning("An observer raised. The run goes on.", exc_info=True)
            continue
        if isinstance(raced, CutShort):
            _log.warning("An observer took more than a second and was cancelled.")
