"""The chats whose tools change the made-up world, each reaching another order.

The first nine cut a change in each way a run can cut it. The other eight
were built to find a wrong order: a stop or a limit on a change next to
other calls, and the same change again and again.
"""

from dataclasses import replace

from lamplighter.ports import ToolCallPart
from tests.kill_kit import Scenario, call, with_limits
from tests.kill_scenarios import calls, text
from tests.run_history import SPEC, inbound, stop


def put(call_id: str, name: str, said: str, tool: str = "put") -> ToolCallPart:
    """A call that puts `said` into the text `name`."""
    return ToolCallPart(id=call_id, name=tool, arguments={"name": name, "text": said})


def shaky(call_id: str, name: str, said: str, lands: bool) -> ToolCallPart:
    """A call to the put that may write or not, and then cannot say."""
    arguments = {"name": name, "text": said, "lands": lands}
    return ToolCallPart(id=call_id, name="shaky_put", arguments=arguments)


MANY_CHANGES = Scenario(
    "a put, a launch, a read, a put that changes nothing, a refused put, and more",
    waiting=(inbound("m1", "Change things."),),
    tools="world",
    script={
        "Change things.": (
            calls(put("c1", "tea", "mint"), call("c2", "launch", what="rocket")),
            calls(call("c3", "note", n=1), put("c4", "tea", "mint")),
            calls(put("c5", "locked-a", "x"), put("c6", "jam", "plum")),
            calls(
                shaky("c7", "cup", "full", lands=True),
                shaky("c8", "pot", "hot", lands=False),
            ),
            text("Changed."),
        ),
    },
)

STOP_DURING_PUT = Scenario(
    "a stop arrives while a put runs",
    waiting=(inbound("m1", "Put slowly."),),
    tools="world",
    during_tool=stop("s1"),
    arrives_on="slow_put",
    script={
        "Put slowly.": (calls(put("c1", "tea", "mint", "slow_put")), text("Put.")),
    },
)

STOP_DURING_STUBBORN_PUT = Scenario(
    "a stop arrives while a put that ignores a cancel runs",
    waiting=(inbound("m1", "Put stubbornly."),),
    tools="world",
    during_tool=stop("s1"),
    arrives_on="stubborn_put",
    script={
        "Put stubbornly.": (
            calls(put("c1", "tea", "mint", "stubborn_put")),
            text("Put."),
        ),
    },
)

MESSAGE_DURING_PUT = Scenario(
    "a message arrives while a put runs",
    waiting=(inbound("m1", "Put slowly."),),
    tools="world",
    during_tool=inbound("m2", "And put jam."),
    arrives_on="slow_put",
    script={
        "Put slowly.": (calls(put("c1", "tea", "mint", "slow_put")), text("Put.")),
        "And put jam.": (calls(put("c2", "jam", "plum")), text("Put jam.")),
    },
)

TIME_LIMIT_ON_PUT = Scenario(
    "the time limit cuts a put",
    waiting=(inbound("m1", "Put slowly."),),
    tools="world",
    spec=with_limits(max_seconds=3),
    script={
        "Put slowly.": (calls(put("c1", "tea", "mint", "slow_put")), text("Put.")),
    },
)

SETTINGS_CHANGE_WITH_CHANGES = Scenario(
    "the settings change at the restart, with changes open",
    waiting=(inbound("m1", "Change things."),),
    tools="world",
    spec_after=replace(SPEC, config_id="d" * 64),
    script=MANY_CHANGES.script,
)

MESSAGE_STOP_MESSAGE_WITH_CHANGES = Scenario(
    "a message, a stop and a message, with changes",
    waiting=(
        inbound("m1", "Put tea."),
        stop("s1"),
        inbound("m2", "Put jam."),
    ),
    tools="world",
    script={
        "Put tea.": (calls(put("c1", "tea", "mint")), text("Put tea.")),
        "Put jam.": (calls(put("c2", "jam", "plum")), text("Put jam.")),
    },
)

BIG_OUTSIDE_AND_INVALID = Scenario(
    "an output over the cap, an outside text, and arguments that do not fit",
    waiting=(inbound("m1", "Read big things."),),
    tools="world",
    script={
        "Read big things.": (
            calls(call("c1", "big", size=31_000), call("c2", "page")),
            calls(
                ToolCallPart(id="c3", name="put", arguments={"name": "tea"}),
                put("c4", "tea", "mint"),
            ),
            text("Read."),
        ),
    },
)

STEP_LIMIT_WITH_CHANGES = Scenario(
    "the step limit, with changes",
    waiting=(inbound("m1", "Put for ever."),),
    tools="world",
    spec=with_limits(max_steps=3),
    script={
        "Put for ever.": tuple(calls(put(f"c{n}", f"t{n}", "x")) for n in range(1, 6)),
    },
)

STOP_ON_THE_FIRST_OF_FOUR = Scenario(
    "a stop on the first of four calls",
    waiting=(inbound("m1", "Put four."),),
    tools="world",
    during_tool=stop("s1"),
    arrives_on="slow_put",
    script={
        "Put four.": (
            calls(
                put("c1", "a", "1", "slow_put"),
                put("c2", "b", "2"),
                call("c3", "note", n=3),
                put("c4", "d", "4"),
            ),
            text("Put four."),
        ),
    },
)

STOP_ON_A_READ_BETWEEN_CHANGES = Scenario(
    "a stop on a read between two changes",
    waiting=(inbound("m1", "Put, note, put."),),
    tools="world",
    during_tool=stop("s1"),
    arrives_on="note",
    script={
        "Put, note, put.": (
            calls(put("c1", "a", "1"), call("c2", "note", n=2), put("c3", "c", "3")),
            text("Done."),
        ),
    },
)

TIME_LIMIT_ON_STUBBORN_PUT = Scenario(
    "the time limit on a put that ignores a cancel",
    waiting=(inbound("m1", "Put stubbornly."),),
    tools="world",
    spec=with_limits(max_seconds=3),
    script={
        "Put stubbornly.": (
            calls(put("c1", "tea", "mint", "stubborn_put")),
            text("Put."),
        ),
    },
)

SAME_REFUSED_CHANGE = Scenario(
    "the same refused change, again and again",
    waiting=(inbound("m1", "Put into the locked text."),),
    tools="world",
    script={
        "Put into the locked text.": tuple(
            calls(put(f"c{n}", "locked-a", "x")) for n in range(1, 13)
        ),
    },
)

SAME_CHANGE = Scenario(
    "the same change, again and again",
    waiting=(inbound("m1", "Put mint into tea."),),
    tools="world",
    script={
        "Put mint into tea.": tuple(
            calls(put(f"c{n}", "tea", "mint")) for n in range(1, 13)
        ),
    },
)

MESSAGE_DURING_STUBBORN_PUT = Scenario(
    "a message arrives while a put that ignores a cancel runs",
    waiting=(inbound("m1", "Put stubbornly."),),
    tools="world",
    during_tool=inbound("m2", "And put jam."),
    arrives_on="stubborn_put",
    script={
        "Put stubbornly.": (
            calls(put("c1", "tea", "mint", "stubborn_put")),
            text("Put."),
        ),
        "And put jam.": (calls(put("c2", "jam", "plum")), text("Put jam.")),
    },
)

SETTINGS_CHANGE_WITH_A_STOP = Scenario(
    "the settings change at the restart, with a stop waiting",
    waiting=(inbound("m1", "Put slowly."),),
    tools="world",
    during_tool=stop("s1"),
    arrives_on="slow_put",
    spec_after=replace(SPEC, config_id="d" * 64),
    script={
        "Put slowly.": (
            calls(put("c1", "tea", "mint", "slow_put"), put("c2", "jam", "plum")),
            text("Put."),
        ),
    },
)

STEP_LIMIT_WITH_TWO_CHANGES_EACH = Scenario(
    "the step limit, with two changes in each reply",
    waiting=(inbound("m1", "Put pairs for ever."),),
    tools="world",
    spec=with_limits(max_steps=2),
    script={
        "Put pairs for ever.": tuple(
            calls(put(f"c{n}a", f"a{n}", "x"), put(f"c{n}b", f"b{n}", "y"))
            for n in range(1, 5)
        ),
    },
)

CHANGES = (
    MANY_CHANGES,
    STOP_DURING_PUT,
    STOP_DURING_STUBBORN_PUT,
    MESSAGE_DURING_PUT,
    TIME_LIMIT_ON_PUT,
    SETTINGS_CHANGE_WITH_CHANGES,
    MESSAGE_STOP_MESSAGE_WITH_CHANGES,
    BIG_OUTSIDE_AND_INVALID,
    STEP_LIMIT_WITH_CHANGES,
    STOP_ON_THE_FIRST_OF_FOUR,
    STOP_ON_A_READ_BETWEEN_CHANGES,
    TIME_LIMIT_ON_STUBBORN_PUT,
    SAME_REFUSED_CHANGE,
    SAME_CHANGE,
    MESSAGE_DURING_STUBBORN_PUT,
    SETTINGS_CHANGE_WITH_A_STOP,
    STEP_LIMIT_WITH_TWO_CHANGES_EACH,
)

ONE_PUT = Scenario(
    "one put, then an answer",
    waiting=(inbound("m1", "Put mint into tea."),),
    tools="world",
    script={"Put mint into tea.": (calls(put("c1", "tea", "mint")), text("Put."))},
)
