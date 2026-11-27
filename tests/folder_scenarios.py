"""The nine chats that change the made-up world, again with the built-in tools.

Each has the shape of one in `change_scenarios.py`: the file tools do the
puts, and `shell` the launch and the put that runs long. The world is a
real folder, and time stands still unless a command is to outlast a limit.
"""

from dataclasses import replace

from lamplighter.ports import ToolCallPart
from tests.builtin_kit import PAGE_URL
from tests.kill_kit import Scenario, with_limits
from tests.kill_scenarios import calls, text
from tests.run_history import SPEC, inbound, stop


def write(call_id: str, path: str, content: str) -> ToolCallPart:
    """A call that writes `content` as the whole of the file at `path`."""
    arguments = {"path": path, "content": content}
    return ToolCallPart(id=call_id, name="write_file", arguments=arguments)


def edit(call_id: str, path: str, old: str, new: str) -> ToolCallPart:
    """A call that changes `old` to `new` in the file at `path`."""
    arguments = {"path": path, "old_text": old, "new_text": new}
    return ToolCallPart(id=call_id, name="edit_file", arguments=arguments)


def run(call_id: str, line: str) -> ToolCallPart:
    """A call that runs one command line."""
    return ToolCallPart(id=call_id, name="shell", arguments={"command": line})


def read(call_id: str, path: str) -> ToolCallPart:
    """A call that reads the file at `path`."""
    return ToolCallPart(id=call_id, name="read_file", arguments={"path": path})


# A command that outlasts any stop or limit of a test.
LONG = "sleep 30"

MANY_CHANGES = Scenario(
    "a write, a command, a read, a write that changes nothing, a refused write,"
    " and edits",
    waiting=(inbound("m1", "Change things."),),
    tools="folder",
    clock="still",
    script={
        "Change things.": (
            calls(
                write("c1", "tea.txt", "mint\n"), run("c2", "echo x >> launches.txt")
            ),
            calls(read("c3", "tea.txt"), write("c4", "tea.txt", "mint\n")),
            calls(
                write("c5", "../outside/decoy.txt", "changed"),
                write("c6", "jam.txt", "plum\n"),
            ),
            calls(
                edit("c7", "tea.txt", "mint", "green"),
                edit("c8", "pot.txt", "hot", "cold"),
            ),
            text("Changed."),
        ),
    },
)

STOP_DURING_WRITE = Scenario(
    "a stop arrives while a write runs",
    waiting=(inbound("m1", "Write tea."),),
    tools="folder",
    clock="still",
    during_tool=stop("s1"),
    arrives_on="write_file",
    script={"Write tea.": (calls(write("c1", "tea.txt", "mint\n")), text("Wrote."))},
)

STOP_DURING_LONG_COMMAND = Scenario(
    "a stop arrives while a long command runs",
    waiting=(inbound("m1", "Run long."),),
    tools="folder",
    clock="still",
    during_tool=stop("s1"),
    arrives_on="shell",
    script={"Run long.": (calls(run("c1", LONG)), text("Ran."))},
)

MESSAGE_DURING_WRITE = Scenario(
    "a message arrives while a write runs",
    waiting=(inbound("m1", "Write tea."),),
    tools="folder",
    clock="still",
    during_tool=inbound("m2", "And write jam."),
    arrives_on="write_file",
    script={
        "Write tea.": (calls(write("c1", "tea.txt", "mint\n")), text("Wrote.")),
        "And write jam.": (
            calls(write("c2", "jam.txt", "plum\n")),
            text("Wrote jam."),
        ),
    },
)

TIME_LIMIT_ON_COMMAND = Scenario(
    "the time limit cuts a long command",
    waiting=(inbound("m1", "Run long."),),
    tools="folder",
    spec=with_limits(max_seconds=3),
    script={"Run long.": (calls(run("c1", LONG)), text("Ran."))},
)

SETTINGS_CHANGE_WITH_CHANGES = Scenario(
    "the settings change at the restart, with file changes open",
    waiting=(inbound("m1", "Change things."),),
    tools="folder",
    clock="still",
    spec_after=replace(SPEC, config_id="d" * 64),
    script=MANY_CHANGES.script,
)

MESSAGE_STOP_MESSAGE_WITH_CHANGES = Scenario(
    "a message, a stop and a message, with file changes",
    waiting=(
        inbound("m1", "Write tea."),
        stop("s1"),
        inbound("m2", "Write jam."),
    ),
    tools="folder",
    clock="still",
    script={
        "Write tea.": (calls(write("c1", "tea.txt", "mint\n")), text("Wrote tea.")),
        "Write jam.": (calls(write("c2", "jam.txt", "plum\n")), text("Wrote jam.")),
    },
)

BIG_OUTSIDE_AND_INVALID = Scenario(
    "a command's output over the cap, a page, and arguments that do not fit",
    waiting=(inbound("m1", "Read big things."),),
    tools="folder",
    clock="still",
    script={
        "Read big things.": (
            calls(
                run("c1", "head -c 31000 /dev/zero | tr '\\0' x"),
                ToolCallPart(
                    id="c2",
                    name="web_fetch",
                    arguments={"url": PAGE_URL},
                ),
            ),
            calls(
                ToolCallPart(id="c3", name="write_file", arguments={"path": "tea.txt"}),
                write("c4", "tea.txt", "mint\n"),
            ),
            text("Read."),
        ),
    },
)

STEP_LIMIT_WITH_CHANGES = Scenario(
    "the step limit, with file changes",
    waiting=(inbound("m1", "Write for ever."),),
    tools="folder",
    clock="still",
    spec=with_limits(max_steps=3),
    script={
        "Write for ever.": tuple(
            calls(write(f"c{n}", f"t{n}.txt", "x\n")) for n in range(1, 6)
        ),
    },
)

IN_A_FOLDER = (
    MANY_CHANGES,
    STOP_DURING_WRITE,
    STOP_DURING_LONG_COMMAND,
    MESSAGE_DURING_WRITE,
    TIME_LIMIT_ON_COMMAND,
    SETTINGS_CHANGE_WITH_CHANGES,
    MESSAGE_STOP_MESSAGE_WITH_CHANGES,
    BIG_OUTSIDE_AND_INVALID,
    STEP_LIMIT_WITH_CHANGES,
)
