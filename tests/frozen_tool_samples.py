"""The rows of the frozen stores of tool calls: every event as the loop writes it.

The text files tests/stores/version_2_tools.sql,
tests/stores/version_2_tools_with_model.sql,
tests/stores/version_2_tools_with_revision.sql and
tests/stores/version_2_routes.sql were each made once, by running
these chats through the loop of their day on the SQLite adapters and
dumping the file as SQL. The text files are never edited, and these chats
are not changed. The test compares the newer file with what the in-memory
stores hold after the same chats, so a change that reads old data
differently fails it. The chats leave an effect row in each state the
ledger can reach.
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from lamplighter.adapters import (
    CountingIds,
    MadeUpWorld,
    ScriptedReply,
    WorldTools,
)
from lamplighter.core import Deps, Observers, RunningSessions, RunSpec, run_turn
from lamplighter.ports import (
    Action,
    BlobStore,
    Effect,
    EffectLedger,
    EventLog,
    Failure,
    Inbox,
    JsonObject,
    Outbox,
    Plan,
    TextPart,
    ToolCallPart,
    ToolEntry,
    ToolResult,
    Usage,
)
from tests.kill_kit import MirroredLedger, answers
from tests.model_by_request import ModelByRequest
from tests.run_history import MODEL_NAME, SPEC, inbound, stop
from tests.waiting_clock import JumpingClock

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
GONE = Failure(kind="unavailable", effect="none", message="Gone.", source="t")


def _calls(*parts: ToolCallPart) -> ScriptedReply:
    return ScriptedReply(parts, "tool_calls", USAGE)


def _text(said: str) -> ScriptedReply:
    return ScriptedReply((TextPart(text=said),), "end", USAGE)


def _call(call_id: str, tool: str, **arguments: Any) -> ToolCallPart:
    return ToolCallPart(id=call_id, name=tool, arguments=arguments)


SCRIPT = {
    "Change things.": (
        _calls(
            _call("c1", "put", name="tea", text="mint"),
            _call("c2", "big", size=31_000),
            _call("c3", "page"),
        ),
        _calls(
            _call("c4", "put", name="tea"),
            _call("c5", "shaky_put", name="pot", text="hot", lands=False),
        ),
        _text("Changed."),
    ),
    "Put slowly.": (_calls(_call("c6", "slow_put", name="jam", text="plum")),),
    "Note for ever.": (
        _calls(_call("c7", "note", n=1)),
        _calls(_call("c8", "note", n=2)),
    ),
    "Launch.": (_calls(_call("c9", "launch", what="rocket")), _text("Launched.")),
    "Put cake.": (_calls(_call("c10", "put", name="cake", text="lemon")),),
}


class _Hooked:
    """The made-up world's tools, with a hook as a plan starts."""

    def __init__(self, inner: WorldTools) -> None:
        self.inner = inner
        self.on_plan: Callable[[], Awaitable[None]] | None = None

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self.inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        return await self.inner.call(name, arguments)

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        if self.on_plan is not None:
            await self.on_plan()
        return await self.inner.plan(name, arguments)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        return await self.inner.apply(name, arguments, plan)

    async def check(self, probe: JsonObject) -> bool | None:
        return await self.inner.check(probe)


class _Breaking:
    """A ledger whose next `begin` or `finish` fails once, when told to."""

    def __init__(self, inner: EffectLedger) -> None:
        self.inner = inner
        self.fail: str | None = None

    async def begin(
        self, action: Action, probe: JsonObject | None, undo: JsonObject | None
    ) -> Any:
        begun = await self.inner.begin(action, probe, undo)
        return self._once("begin", begun)

    async def finish(self, effect_id: int, result: Any) -> Any:
        if self.fail == "finish":
            self.fail = None
            return GONE
        return await self.inner.finish(effect_id, result)

    async def resolve(self, effect_id: int, outcome: Any) -> Any:
        return await self.inner.resolve(effect_id, outcome)

    async def unresolved(self) -> Any:
        return await self.inner.unresolved()

    def _once(self, name: str, value: object) -> object:
        if self.fail == name:
            self.fail = None
            return GONE
        return value


async def write_tool_samples(
    log: EventLog, inbox: Inbox, outbox: Outbox, ledger: EffectLedger, blobs: BlobStore
) -> tuple[Action, ...]:
    """Run the chats, and give the action of every effect row they made."""
    rows: dict[int, Effect] = {}
    breaking = _Breaking(MirroredLedger(ledger, rows))
    clock = JumpingClock()
    running = RunningSessions()
    tools = _Hooked(WorldTools(MadeUpWorld(), clock))
    deps = Deps(
        log=log,
        inbox=inbox,
        outbox=outbox,
        models={MODEL_NAME: ModelByRequest(answers(SCRIPT))},
        health={},
        tools=tools,
        ledger=breaking,
        blobs=blobs,
        clock=clock,
        ids=CountingIds(),
        observers=Observers(),
        running=running,
    )

    async def until_idle(session: str, spec: RunSpec = SPEC) -> None:
        # A run that ends, or a store that failed once, sends it round again.
        while await run_turn(session, spec, deps) is not None:
            pass

    # A put done, a cut result, outside text, arguments that do not fit, and
    # a put that failed and could not say: done and resolved rows.
    await inbox.accept(inbound("m1", "Change things."))
    await until_idle("sess-a")

    # A stop known before the apply starts: a failed row, and `cancelled`.
    async def stop_now() -> None:
        await inbox.accept(stop("s1"))
        running.ask_stop("sess-a")

    tools.on_plan = stop_now
    await inbox.accept(inbound("m2", "Put slowly."))
    await until_idle("sess-a")
    tools.on_plan = None

    # The step limit: `limit_hit`.
    await inbox.accept(inbound("m3", "Note for ever."))
    await until_idle("sess-a", spec=replace(SPEC, max_steps=1))

    # A launch whose `finish` is lost, settled with no probe: unknown.
    await inbox.accept(inbound("m4", "Launch.", session="sess-b"))
    breaking.fail = "finish"
    await until_idle("sess-b")

    # A row begun, and the process gone before the intent: intended.
    await inbox.accept(inbound("m5", "Put cake.", session="sess-c"))
    breaking.fail = "begin"
    assert isinstance(await run_turn("sess-c", SPEC, deps), Failure)
    tools.inner.gone()
    return tuple(row.action for _, row in sorted(rows.items()))
