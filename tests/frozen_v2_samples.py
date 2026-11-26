"""The rows of the frozen version 2 store: every event type and outbox state.

The text file tests/stores/version_2.sql was made once, by running these
chats through the loop on the SQLite adapters and dumping the file as SQL.
Neither the text file nor this one is edited after that. The test that
reads the text file compares it with what the in-memory stores hold after
the same chats, so a change that reads old data differently fails it.
"""

from dataclasses import replace

from lamplighter.adapters import CountingIds, ScriptedReply, ScriptedTool, ScriptedTools
from lamplighter.core import Deps, Observers, RunningSessions, run_turn
from lamplighter.ports import (
    DeliveryReceipt,
    EventLog,
    Failure,
    Inbox,
    JsonObject,
    Outbox,
    TextPart,
    ToolResult,
    Usage,
)
from tests.kill_kit import NOTE, TICK, WAIT, Killed, Turn, answers, call
from tests.model_by_request import ModelByRequest
from tests.run_history import SPEC, inbound, stop
from tests.waiting_clock import JumpingClock

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
SENT = DeliveryReceipt(at="2026-11-26T07:35:13.000Z")
MAYBE_SENT = Failure(kind="timeout", effect="unknown", message="No answer.", source="t")


def _calls(*parts: object) -> ScriptedReply:
    return ScriptedReply(tuple(parts), "tool_calls", USAGE)  # type: ignore[arg-type]


def _text(said: str) -> ScriptedReply:
    return ScriptedReply((TextPart(text=said),), "end", USAGE)


SCRIPT: dict[str, tuple[Turn, ...]] = {
    "Note one, and look up the weather.": (
        _calls(call("c1", "note", n=1), call("c2", "weather")),
        _text("Noted one. There is no weather tool."),
    ),
    "Note two, then three.": (
        _calls(call("c3", "note", n=2), call("c4", "note", n=3)),
        _text("Noted both."),
    ),
    "Tick for ever.": (_calls(call("c5", "tick")), _calls(call("c6", "tick"))),
    "Wait a second.": (_calls(call("c7", "wait", seconds=1)), _text("Sorry.")),
}


class _World:
    """The parts the chats share, with a stop that comes during a note."""

    def __init__(self, inbox: Inbox) -> None:
        self.inbox = inbox
        self.running = RunningSessions()
        self.killed_once = False

    def tools(self) -> ScriptedTools:
        async def note(arguments: JsonObject) -> ToolResult:
            if arguments.get("n") == 2:
                # A stop comes in while this note runs, so the next is not run.
                await self.inbox.accept(stop("s1"))
                self.running.ask_stop("sess-a")
            return ToolResult(
                text=f"Noted {arguments.get('n')}.", is_error=False, effect="none"
            )

        async def tick(arguments: JsonObject) -> ToolResult:
            del arguments
            return ToolResult(text="Tick.", is_error=False, effect="none")

        async def wait(arguments: JsonObject) -> ToolResult:
            del arguments
            if not self.killed_once:
                self.killed_once = True
                raise Killed
            return ToolResult(text="Waited.", is_error=False, effect="none")

        return ScriptedTools(
            [
                ScriptedTool(NOTE, note),
                ScriptedTool(TICK, tick),
                ScriptedTool(WAIT, wait),
            ]
        )


async def write_v2_samples(log: EventLog, inbox: Inbox, outbox: Outbox) -> None:
    """Run the chats, then move the outbox rows to each of their states."""
    world = _World(inbox)
    deps = Deps(
        log=log,
        inbox=inbox,
        outbox=outbox,
        model=ModelByRequest(answers(SCRIPT)),
        tools=world.tools(),
        clock=JumpingClock(),
        ids=CountingIds(),
        observers=Observers(),
        running=world.running,
    )
    for native_id, text in (
        ("m1", "Note one, and look up the weather."),
        ("m2", "Note two, then three."),
    ):
        await inbox.accept(inbound(native_id, text))
        while await run_turn("sess-a", SPEC, deps) is not None:
            pass
    await inbox.accept(inbound("m3", "Tick for ever."))
    while await run_turn("sess-a", replace(SPEC, max_steps=1), deps) is not None:
        pass
    await inbox.accept(inbound("m4", "Wait a second.", session="sess-b"))
    try:
        await run_turn("sess-b", SPEC, deps)
    except Killed:
        world.running.end("sess-b")
    while await run_turn("sess-b", SPEC, deps) is not None:
        pass
    await _move_the_outbox(outbox)


async def _move_the_outbox(outbox: Outbox) -> None:
    due = await outbox.due()
    assert not isinstance(due, Failure) and len(due) == 4, due
    sent, sending, failed, _ = due
    for out in (sent, sending, failed):
        assert await outbox.start_send(out.out_id) is None
    assert await outbox.mark_sent(sent.out_id, SENT) is None
    # A send that may have shown the message failed, so the row is pending
    # again and marked as a possible repeat. The last row was never begun.
    assert await outbox.mark_failed(failed.out_id, MAYBE_SENT) is None
