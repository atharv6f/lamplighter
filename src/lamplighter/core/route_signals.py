"""The signals that move a run up its ladder, read from its events. Pure.

Two signals are read from the tool results saved since the run's last
choice, and once there they stand until the next choice. The other four
come from how the last reply would end the run.
"""

from collections.abc import Sequence

from lamplighter.core.event_types import TOOL_RESULT
from lamplighter.core.harness_texts import without_repeat_warning
from lamplighter.core.model_choice import Signal
from lamplighter.ports import Event

# The results the harness writes for a call the model got wrong.
_INVALID_CALL_KINDS = frozenset(
    {"arguments_unparsed", "arguments_invalid", "unknown_tool"}
)
# How a run would end, by its detail, for the four signals of the move.
_ENDING_SIGNALS: dict[str, Signal] = {
    "empty_reply": "empty_reply",
    "refused": "refused",
    "model_too_large": "too_large",
}


def standing_signal(after_choice: Sequence[Event]) -> Signal | None:
    """Give the signal that stands in the events after the run's last choice.

    `invalid_calls` stands once two tool results in a row were the
    harness's answers to calls the model got wrong. `repeated_call` stands
    once a result carried the repeat warning. When both stand, the newer is
    given.
    """
    streak = 0
    invalid: int | None = None
    repeated: int | None = None
    for place, event in enumerate(after_choice):
        if event.type != TOOL_RESULT:
            continue
        wrong = event.payload.get("harness") in _INVALID_CALL_KINDS
        streak = streak + 1 if wrong else 0
        if streak == 2 and invalid is None:
            invalid = place
        text = event.payload.get("text")
        warned = isinstance(text, str) and without_repeat_warning(text) != text
        if warned and repeated is None:
            repeated = place
    if invalid is None and repeated is None:
        return None
    if repeated is None or (invalid is not None and invalid > repeated):
        return "invalid_calls"
    return "repeated_call"


def ending_signal(detail: str) -> Signal | None:
    """Give the signal of a run end, by its detail, or None for an end that is none.

    Every `model_` end but `model_too_large` is `model_failed`: a request
    one server cannot take, another may.
    """
    if detail in _ENDING_SIGNALS:
        return _ENDING_SIGNALS[detail]
    if detail.startswith("model_"):
        return "model_failed"
    return None
