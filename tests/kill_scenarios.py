"""The chats the kill tests kill, each built to reach a different order of writes."""

from dataclasses import replace

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.ports import Failure, TextPart, Usage
from tests.kill_kit import Scenario, call, with_limits
from tests.run_history import SPEC, inbound, stop

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
LOST = Failure(kind="transient", effect="none", message="The stream broke.", source="t")


def calls(*parts: object) -> ScriptedReply:
    """A reply that asks for these tool calls."""
    return ScriptedReply(tuple(parts), "tool_calls", USAGE)  # type: ignore[arg-type]


def text(said: str) -> ScriptedReply:
    """A final reply."""
    return ScriptedReply((TextPart(text=said),), "end", USAGE)


EMPTY = ScriptedReply((), "end", USAGE)

THREE_STEPS = Scenario(
    "three steps with two tools and a tool that does not exist",
    waiting=(inbound("m1", "Note one and tick."),),
    script={
        "Note one and tick.": (
            calls(call("c1", "note", n=1), call("c2", "tick")),
            calls(call("c3", "weather")),
            text("First answer."),
        ),
    },
)

MESSAGE_STOP_MESSAGE = Scenario(
    "a message, a stop and another message, all waiting at the start",
    waiting=(
        inbound("m1", "Note one."),
        stop("s1"),
        inbound("m2", "Now note two."),
    ),
    script={
        "Note one.": (calls(call("c1", "note", n=1)), text("Noted one.")),
        "Now note two.": (calls(call("c2", "note", n=2)), text("Noted two.")),
    },
)

STOP_FIRST = Scenario(
    "a stop that arrives before any message",
    waiting=(stop("s1"), inbound("m1", "Tick.")),
    script={"Tick.": (calls(call("c1", "tick")), text("Ticked."))},
)

MESSAGE_DURING_TOOL = Scenario(
    "a message arrives while a tool runs",
    waiting=(inbound("m1", "Wait a little."),),
    during_tool=inbound("m2", "And note three."),
    script={
        "Wait a little.": (calls(call("c1", "wait", seconds=5)), text("Waited.")),
        "And note three.": (calls(call("c2", "note", n=3)), text("Noted three.")),
    },
)

STOP_DURING_TOOL = Scenario(
    "a stop arrives while a tool runs",
    waiting=(inbound("m1", "Wait a little."),),
    during_tool=stop("s1"),
    script={"Wait a little.": (calls(call("c1", "wait", seconds=5)), text("Waited."))},
)

STEP_LIMIT = Scenario(
    "the step limit",
    waiting=(inbound("m1", "Tick for ever."),),
    spec=with_limits(max_steps=3),
    script={
        "Tick for ever.": (
            calls(call("c1", "tick")),
            calls(call("c2", "tick")),
            calls(call("c3", "tick")),
            calls(call("c4", "tick")),
        ),
    },
)

TIME_LIMIT = Scenario(
    "the time limit cuts a tool",
    waiting=(inbound("m1", "Wait a long time."),),
    spec=with_limits(max_seconds=10),
    script={
        "Wait a long time.": (calls(call("c1", "wait", seconds=5_000)), text("Done.")),
    },
)

REPEATED_CALL = Scenario(
    "the same call, repeated",
    waiting=(inbound("m1", "Note one again and again."),),
    script={
        # More replies than the rule lets run, each with its own call id, so
        # a run whose count began again after a kill still has calls left.
        "Note one again and again.": tuple(
            calls(call(f"c{n}", "note", n=1)) for n in range(1, 13)
        ),
    },
)

SETTINGS_CHANGE = Scenario(
    "the settings change at the restart",
    waiting=(inbound("m1", "Note one and tick."),),
    spec_after=replace(SPEC, config_id="d" * 64),
    script=THREE_STEPS.script,
)

TWO_BAD_REPLIES = Scenario(
    "two bad replies",
    waiting=(inbound("m1", "Tick."),),
    script={
        "Tick.": (
            ScriptedBreak((TextPart(text="Let me "),), LOST),
            ScriptedBreak((), LOST),
            text("Ticked."),
        ),
    },
)

EMPTY_THEN_TEXT = Scenario(
    "an empty reply, then text",
    waiting=(inbound("m1", "Say something."),),
    script={"Say something.": (EMPTY, text("Something."))},
)

MANY_WAITING = Scenario(
    "message, message, stop, message, stop",
    waiting=(
        inbound("m1", "Note one."),
        inbound("m2", "Note two."),
        stop("s1"),
        inbound("m3", "Tick."),
        stop("s2"),
    ),
    script={
        "Note one.": (calls(call("c1", "note", n=1)), text("Noted one.")),
        "Note two.": (calls(call("c2", "note", n=2)), text("Noted two.")),
        "Tick.": (calls(call("c3", "tick")), text("Ticked.")),
    },
)

ALL = (
    THREE_STEPS,
    MESSAGE_STOP_MESSAGE,
    STOP_FIRST,
    MESSAGE_DURING_TOOL,
    STOP_DURING_TOOL,
    STEP_LIMIT,
    TIME_LIMIT,
    REPEATED_CALL,
    SETTINGS_CHANGE,
    TWO_BAD_REPLIES,
    EMPTY_THEN_TEXT,
    MANY_WAITING,
)
