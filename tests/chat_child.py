"""A real chat process with a made-up model, for tests that kill it or type into it.

    python -m tests.chat_child FOLDER BEHAVIOUR [SESSION [GROUPS]]

It runs the real `chat` command on a real store in FOLDER, with the stub
tools and a model that picks its reply from the request. BEHAVIOUR picks
how the model and the tools act. Every tool start is written to the file
`started.txt` in FOLDER, one line each, before the tool runs. A write
reaches the operating system at once, so a kill does not lose it.

When its input is a terminal, the child makes that terminal its own, so a
Ctrl-C typed there reaches it as a signal.
"""

import asyncio
import dataclasses
import fcntl
import os
import sqlite3
import sys
import termios
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters import (
    ScriptedReply,
    ScriptedTool,
    ScriptedTools,
    StubTools,
    SystemClock,
)
from lamplighter.adapters.sqlite import SqliteEventLog, SqliteInbox, open_database
from lamplighter.app.chat import chat
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import chat_parts
from lamplighter.ports import (
    Failure,
    Inbound,
    JsonObject,
    ModelRequest,
    Plan,
    TextPart,
    ToolCallPart,
    ToolEntry,
    ToolProvider,
    ToolResult,
    Usage,
    canonical_json,
)
from tests.kill_kit import Turn, answers, call, last_said
from tests.model_by_request import Choose, ModelByRequest
from tests.model_samples import PROFILE
from tests.run_history import inbound, stop

ROOT = Path(__file__).parents[1]
SESSION = "sess-a"
STARTED = "started.txt"
SEEDED_ALL = "seeded-all"
_USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)


def _calls(*parts: object) -> ScriptedReply:
    return ScriptedReply(tuple(parts), "tool_calls", _USAGE)  # type: ignore[arg-type]


def _text(said: str) -> ScriptedReply:
    return ScriptedReply((TextPart(text=said),), "end", _USAGE)


# What the person types in the terminal tests, and what the model does.
TYPED: dict[str, tuple[Turn, ...]] = {
    "Hello.": (_text("Hi there."),),
    "Wait a minute.": (_calls(call("w1", "wait", seconds=60)), _text("Waited.")),
    "Wait a second.": (
        _calls(call("w2", "wait", seconds=1)),
        _text("Waited a second."),
    ),
}

# The kinds of group the kill test sends in, one group at a time. A group
# is sent only when nothing waits and no run is open, so each starts its own
# run. The stop waits behind its message, so it ends that message's run.
_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Note the time and wait.",),
    ("Wait again.", "stop"),
    ("What time is it?",),
    ("Wait once more.",),
)


def arrivals(groups: int) -> list[tuple[Inbound, ...]]:
    """Give `groups` groups of messages, the four kinds in turn, each id new."""
    made: list[tuple[Inbound, ...]] = []
    for number in range(groups):
        kind = _GROUPS[number % len(_GROUPS)]
        made.append(
            tuple(
                stop(f"s{number}") if text == "stop" else inbound(f"m{number}", text)
                for text in kind
            )
        )
    return made


def sent_in(groups: int) -> int:
    """Give how many messages, not counting stops, the groups hold."""
    return sum(
        1 for group in arrivals(groups) for made in group if made.kind == "message"
    )


KILLED: dict[str, tuple[Turn, ...]] = {
    "Note the time and wait.": (
        _calls(call("k1", "current_time"), call("k2", "wait", seconds=1)),
        _calls(call("k3", "current_time")),
        _text("Waited once."),
    ),
    "Wait again.": (_calls(call("k4", "wait", seconds=1)), _text("Waited again.")),
    "What time is it?": (_calls(call("k5", "current_time")), _text("It is late.")),
    "Wait once more.": (_calls(call("k6", "wait", seconds=1)), _text("Done waiting.")),
}


def said_length(request: ModelRequest) -> Turn:
    """Answer with the length of the person's latest text, in bytes."""
    said, _ = last_said(request)
    return _text(f"Got {len(said.encode())} bytes.")


@dataclass(frozen=True)
class Behaviour:
    """How the model and the tools act.

    `stubborn` makes the wait tool ignore a cancel. Its run then gets a long
    cancel bound, so a second Ctrl-C has time to land before the run ends.
    """

    choose: Choose
    model_seconds: float = 0.0
    stubborn: bool = False
    cancel_bound_ms: int = 800


BEHAVIOURS = {
    "answers": Behaviour(answers(TYPED)),
    "slow_model": Behaviour(answers(TYPED), model_seconds=60.0),
    "stubborn": Behaviour(answers(TYPED), stubborn=True, cancel_bound_ms=10_000),
    "kills": Behaviour(answers(KILLED), model_seconds=0.2),
    "lengths": Behaviour(said_length),
}


def numbered(choose: Choose) -> Choose:
    """Make each call id unique in the session, as a real model's are.

    The script gives the same ids each time a text comes back, so each id
    gets the count of the person's texts so far. That count depends only
    on the request, so a request made again gets the same ids.
    """

    def pick(request: ModelRequest) -> Turn:
        turn = choose(request)
        said = sum(
            1
            for message in request.messages
            if message.role == "user"
            and any(isinstance(part, TextPart) for part in message.parts)
        )
        parts = tuple(
            dataclasses.replace(part, id=f"{part.id}-{said}")
            if isinstance(part, ToolCallPart)
            else part
            for part in turn.parts
        )
        return dataclasses.replace(turn, parts=parts)

    return pick


class RecordedTools:
    """Writes each tool start to a file, then runs it. Satisfies ToolProvider."""

    def __init__(self, inner: ToolProvider, record: Path) -> None:
        self._inner = inner
        self._record = record

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        line = f"{name}\t{canonical_json(arguments)}\n".encode()
        held = os.open(self._record, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(held, line)
        finally:
            os.close(held)
        return await self._inner.call(name, arguments)

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        return await self._inner.plan(name, arguments)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        return await self._inner.apply(name, arguments, plan)

    async def check(self, probe: JsonObject) -> bool | None:
        return await self._inner.check(probe)


def stubborn_tools() -> ScriptedTools:
    """Give `wait`, which goes on to its end when it is cancelled."""
    spec = StubTools(SystemClock()).list_tools()[1].spec

    async def wait(arguments: JsonObject) -> ToolResult:
        seconds = arguments.get("seconds")
        assert isinstance(seconds, int)
        await asyncio.sleep(seconds)
        return ToolResult(
            text=f"Waited {seconds} seconds.", is_error=False, effect="none"
        )

    return ScriptedTools([ScriptedTool(spec, wait, ignores_cancel=True)])


def config_for(folder: Path, cancel_bound_ms: int) -> Config:
    """Give the repo's settings, with the store in `folder` and the test model."""
    config = load_config(ROOT / "lamplighter.toml")
    run = dataclasses.replace(
        config.run, model_max_output=1_000, cancel_bound_ms=cancel_bound_ms
    )
    return dataclasses.replace(
        config,
        model=PROFILE,
        store=dataclasses.replace(config.store, dir=str(folder)),
        run=run,
    )


def send_next_group(folder: Path, groups: int) -> None:
    """Save the next group of messages, if nothing waits and no run is open.

    Once every group is saved, a file says so, and the test stops starting
    the child when a start finds nothing more to do.
    """
    connection = open_database(folder / "state.db", 5.0)
    assert not isinstance(connection, Failure), connection
    try:
        asyncio.run(_send_next_group(folder, connection, groups))
    finally:
        connection.close()


async def _send_next_group(
    folder: Path, connection: sqlite3.Connection, groups: int
) -> None:
    log = SqliteEventLog(connection, {}, 200_000)
    inbox = SqliteInbox(connection, 200_000)
    pending = await inbox.pending()
    unfinished = await log.sessions("unfinished")
    assert not isinstance(pending, Failure) and not isinstance(unfinished, Failure)
    if pending or unfinished:
        return
    for group in arrivals(groups):
        accepted = [await inbox.accept(made) for made in group]
        if any(not isinstance(a, Failure) and a.is_new for a in accepted):
            return
    (folder / SEEDED_ALL).touch()


def main() -> int:
    """Run the chat with the behaviour named on the command line."""
    folder, behaviour = Path(sys.argv[1]), sys.argv[2]
    session = sys.argv[3] if len(sys.argv) > 3 else None
    groups = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    if os.isatty(0):
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    acts = BEHAVIOURS[behaviour]
    folder.mkdir(mode=0o700, exist_ok=True)
    if behaviour == "kills":
        send_next_group(folder, groups)
    clock = SystemClock()
    tools = stubborn_tools() if acts.stubborn else StubTools(clock)
    config = config_for(folder, acts.cancel_bound_ms)
    parts = chat_parts(
        config,
        ModelByRequest(numbered(acts.choose), clock, acts.model_seconds),
        RecordedTools(tools, folder / STARTED),
        sys.stdin.fileno(),
        sys.stdout,
        sys.stderr,
    )
    return chat(config, session, parts)


if __name__ == "__main__":
    sys.exit(main())
