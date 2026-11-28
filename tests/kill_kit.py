"""Kills the chat process at a port call, starts a new one, and checks the stores.

A kill is an exception no code catches, raised just before a call or just
after it has taken effect. The world is what outlives a process: the
stores, the channel, the clock and the record of which tools started.
Each process is a new event loop with new memory on that world, as a
restart would be.
"""

import asyncio
import contextlib
from collections import Counter as Tally
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from lamplighter.adapters import (
    CountingIds,
    MadeUpWorld,
    ScriptedBreak,
    ScriptedChannel,
    ScriptedReply,
    ScriptedTool,
    ScriptedTools,
    WorldTools,
)
from lamplighter.core import (
    Deps,
    Observers,
    RunningSessions,
    RunSpec,
    Wake,
    drive,
    take,
)
from lamplighter.core.harness_texts import NO_EFFECT, TOOK_EFFECT
from lamplighter.ports import (
    Begun,
    Effect,
    EffectLedger,
    EffectResult,
    Event,
    Failure,
    Inbound,
    JsonObject,
    JsonValue,
    ModelRequest,
    Plan,
    TextPart,
    ToolCallPart,
    ToolEntry,
    ToolProvider,
    ToolResult,
    ToolSpec,
    canonical_json,
    saved_json,
)
from lamplighter.ports import (
    Outcome as Settled,
)
from tests.chat_kit import Outcome, Stores, outcome
from tests.model_by_request import Choose, ModelByRequest, check_request
from tests.port_counter import (
    CountedBlobs,
    CountedChannel,
    CountedInbox,
    CountedLedger,
    CountedLog,
    CountedModel,
    CountedOutbox,
    CountedTools,
    Counter,
    When,
)
from tests.projection_checks import quick_wrong
from tests.real_folder import RealFolder, new_folder
from tests.run_history import MODEL_NAME, SESSION, SPEC
from tests.span_checks import spans_wrong
from tests.waiting_clock import JumpingClock, WaitingClock

type Point = tuple[int, When]
type Turn = ScriptedReply | ScriptedBreak

HARNESS_NOTE = "Note from the harness"
EMPTY_NOTE = "your last reply was empty"
INTERRUPTED_ANSWERS = {
    "interrupted",
    "stopped",
    "out_of_time",
    "not_run",
    "unconfirmed",
}
# The changes whose row may be left unknown: two have no probe, and the other
# ignores its cancel, so its check is skipped while it may still be running.
MAY_STAY_UNKNOWN = {"launch", "shell", "stubborn_put"}


class Killed(BaseException):
    """The process dies here. Nothing in the code may catch it."""


def _spec(name: str, properties: JsonObject) -> ToolSpec:
    schema: JsonObject = {"type": "object", "properties": properties}
    return ToolSpec(
        name=name, description=f"A made-up tool, {name}.", arguments_schema=schema
    )


NOTE = _spec("note", {"n": {"type": "integer"}})
TICK = _spec("tick", {})
WAIT = _spec("wait", {"seconds": {"type": "integer"}})


@dataclass(frozen=True)
class Scenario:
    """One chat to kill: what waits at the start, and how the model answers.

    `script` gives, for each text the person wrote, the model's replies in
    order. `during_tool` arrives when the tool named `arrives_on` starts.
    `spec_after` is the spec of every process after the first, as when the
    settings change at a restart. `tools` picks the layer of tools: notes
    that only read, the made-up world that reads and changes, or the
    built-in tools on a real folder. `clock` says whether time jumps
    whenever every task waits, or stands still. The built-in tools wait on
    threads and processes, which a jump would take for a long wait, so for
    them time stands, except where a command is meant to outlast a limit.
    """

    name: str
    waiting: tuple[Inbound, ...]
    script: dict[str, tuple[Turn, ...]]
    spec: RunSpec = SPEC
    during_tool: Inbound | None = None
    arrives_on: str = "wait"
    spec_after: RunSpec | None = None
    tools: Literal["notes", "world", "folder"] = "notes"
    clock: Literal["jumping", "still"] = "jumping"


@dataclass
class World:
    """What outlives every process."""

    stores: Stores = field(default_factory=Stores)
    channel: ScriptedChannel = field(
        default_factory=lambda: ScriptedChannel([], JumpingClock())
    )
    clock: WaitingClock = field(default_factory=JumpingClock)
    ids: CountingIds = field(default_factory=CountingIds)
    started: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    processes: int = 0
    made: MadeUpWorld = field(default_factory=MadeUpWorld)
    rows: dict[int, Effect] = field(default_factory=dict[int, Effect])
    folder: RealFolder | None = None

    @property
    def starts(self) -> list[tuple[str, str]]:
        """Every tool start, of the notes, the made-up world and the folder."""
        folder = [] if self.folder is None else self.folder.started
        return self.started + self.made.started + folder

    @property
    def changes(self) -> list[tuple[str, str]]:
        """Every change, of the made-up world and of the folder."""
        folder = [] if self.folder is None else self.folder.changed
        return self.made.changed + folder


def answers(script: Mapping[str, Sequence[Turn]]) -> Choose:
    """Pick a reply from the latest text the person wrote, and how many came after.

    The pick depends only on the request, so a request made again after a
    kill gets the same reply.
    """

    def choose(request: ModelRequest) -> Turn:
        check_request(request)
        said, after = last_said(request)
        replies = script[said]
        return replies[min(after, len(replies) - 1)]

    return choose


def last_said(request: ModelRequest) -> tuple[str, int]:
    """Give the person's latest text, and the count of replies made since."""
    messages = request.messages
    for place in range(len(messages) - 1, -1, -1):
        message = messages[place]
        if message.role != "user":
            continue
        texts = [
            part.text
            for part in message.parts
            if isinstance(part, TextPart) and not part.text.startswith(HARNESS_NOTE)
        ]
        if texts:
            later = messages[place + 1 :]
            replies = sum(1 for m in later if m.role == "assistant")
            # An empty reply leaves no message, only a note after the text.
            empties = sum(
                1
                for m in messages[place:]
                for part in m.parts
                if isinstance(part, TextPart) and EMPTY_NOTE in part.text
            )
            return texts[-1], replies + empties
    raise AssertionError("a request holds a text the person wrote")


def _tools(world: World, arrive: "Arrival") -> ScriptedTools:
    async def note(arguments: JsonObject) -> ToolResult:
        world.started.append(("note", canonical_json(arguments)))
        return ToolResult(
            text=f"Noted {arguments.get('n')}.", is_error=False, effect="none"
        )

    ticks = [0]

    async def tick(arguments: JsonObject) -> ToolResult:
        world.started.append(("tick", canonical_json(arguments)))
        ticks[0] += 1
        return ToolResult(
            text=f"Tick {ticks[0]} of process {world.processes}.",
            is_error=False,
            effect="none",
        )

    async def wait(arguments: JsonObject) -> ToolResult:
        world.started.append(("wait", canonical_json(arguments)))
        await arrive.now("wait")
        seconds = arguments.get("seconds")
        assert isinstance(seconds, int)
        await world.clock.sleep(seconds)
        return ToolResult(
            text=f"Waited {seconds} seconds.", is_error=False, effect="none"
        )

    return ScriptedTools(
        [ScriptedTool(NOTE, note), ScriptedTool(TICK, tick), ScriptedTool(WAIT, wait)]
    )


@dataclass
class Arrival:
    """A message that arrives while the `wait` tool runs, taken as the intake does."""

    world: World
    made: Inbound | None
    running: RunningSessions
    wake: Wake

    arrives_on: str

    async def now(self, tool: str) -> None:
        if self.made is not None and tool == self.arrives_on:
            stores = self.world.stores
            await take(
                self.made,
                stores.inbox,
                self.world.channel,
                self.running,
                self.wake.wake,
            )


async def _one_process(
    world: World,
    scenario: Scenario,
    spec: RunSpec,
    kill_at: Point | None,
    counter: Counter,
    fail_at: int | None = None,
) -> None:
    async def kill(number: int, when: When, name: str) -> None:
        del name
        if (number, when) == kill_at:
            raise Killed

    counter.hook = kill
    counter.fail_at = fail_at
    running = RunningSessions()
    wake = Wake()
    wake.end_input()
    arrival = Arrival(world, scenario.during_tool, running, wake, scenario.arrives_on)
    stores = world.stores
    made = WorldTools(world.made, world.clock)
    tools: ToolProvider = (
        _tools(world, arrival)
        if scenario.tools == "notes"
        else ArrivingTools(_changing(world, made), arrival)
    )
    deps = Deps(
        log=CountedLog(stores.log, counter),
        inbox=CountedInbox(stores.inbox, counter),
        outbox=CountedOutbox(stores.outbox, counter),
        models={
            MODEL_NAME: CountedModel(ModelByRequest(answers(scenario.script)), counter)
        },
        health={},
        tools=CountedTools(tools, counter),
        ledger=CountedLedger(MirroredLedger(stores.ledger, world.rows), counter),
        blobs=CountedBlobs(stores.blobs, counter),
        clock=world.clock,
        ids=world.ids,
        observers=Observers(),
        running=running,
    )
    channel = CountedChannel(world.channel, counter)
    try:
        # A store failure ends the drive, and the same process calls it
        # again, with what it still holds in memory. With no failure made,
        # the first drive must end well.
        for _ in range(1 if fail_at is None else 2):
            if await drive(SESSION, spec, deps, channel, wake, lambda: None) is None:
                break
        else:
            raise AssertionError("the drive failed twice")
    finally:
        # The process is gone, killed or ended, before its loop shuts down:
        # nothing a tool left running reaches the world after this.
        made.gone()


def _changing(world: World, made: WorldTools) -> ToolProvider:
    if world.folder is None:
        return made
    return world.folder.tools(world.stores.blobs)


def run_process(
    world: World,
    scenario: Scenario,
    kill_at: Point | None = None,
    fail_at: int | None = None,
) -> Counter:
    """Run one process on the world to its end, or to the kill. Give its count.

    With `fail_at`, that store call answers with a failure, and the process
    carries on by driving the chat again.
    """
    spec = (
        scenario.spec
        if world.processes == 0
        else (scenario.spec_after or scenario.spec)
    )
    world.processes += 1
    counter = Counter()
    with contextlib.suppress(Killed):
        asyncio.run(_one_process(world, scenario, spec, kill_at, counter, fail_at))
    if world.folder is not None:
        world.folder.release()
    return counter


def new_world(scenario: Scenario) -> World:
    """Give a world with the scenario's messages waiting in the inbox."""
    world = World(
        clock=JumpingClock() if scenario.clock == "jumping" else WaitingClock(),
        folder=new_folder() if scenario.tools == "folder" else None,
    )

    async def fill() -> None:
        for made in scenario.waiting:
            accepted = await world.stores.inbox.accept(made)
            assert not isinstance(accepted, Failure)

    asyncio.run(fill())
    return world


def points(calls: int) -> list[Point]:
    """Give every point at which a kill can land, in order."""
    return [
        (number, when) for number in range(1, calls + 1) for when in ("before", "after")
    ]


def killed_once(scenario: Scenario, at: Point) -> World:
    """Kill the first process at `at`, then run a second to the end."""
    world = new_world(scenario)
    run_process(world, scenario, at)
    run_process(world, scenario)
    return world


def killed_twice(scenario: Scenario, first: Point, second: Point) -> World:
    """Kill the first process at `first` and the second at `second`, then finish."""
    world = new_world(scenario)
    run_process(world, scenario, first)
    run_process(world, scenario, second)
    run_process(world, scenario)
    return world


def ended(world: World) -> Outcome:
    """Give what the world's stores hold."""
    return asyncio.run(outcome(world.stores, world.channel))


def what_is_wrong(world: World, scenario: Scenario) -> list[str]:
    """Give each rule the world breaks once every process has ended."""
    messages = sum(1 for made in scenario.waiting if made.kind == "message")
    during = scenario.during_tool
    if during is not None and during.kind == "message":
        # It arrives when its tool first starts, if it ever does.
        messages += any(name == scenario.arrives_on for name, _ in world.starts)
    stores = ended(world)
    wrong = broken_rules(stores, world.starts, messages) + _replies(stores)
    wrong += changes_wrong(stores.events, world.rows, world.changes)
    wrong += spans_wrong(stores.events) + quick_wrong(stores.events)
    if world.folder is not None:
        wrong += world.folder.wrong(world.rows)
    return wrong


def broken_rules(
    ended: Outcome, started: Sequence[tuple[str, str]], messages: int
) -> list[str]:
    """Give each rule the stores break, given which tools started.

    `started` holds each tool start as its name and canonical arguments,
    and `messages` is how many messages were sent in. What the channel
    showed is checked apart, because each channel records it its own way.
    """
    wrong = _runs(ended.events) + _calls(ended.events, started)
    wrong += _messages(ended, messages)
    if ended.due:
        wrong.append(f"outbox rows not sent: {ended.due}")
    return wrong


def _runs(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    open_run: str | None = None
    for event in events:
        if event.type == "run_started":
            if open_run is not None:
                wrong.append(f"run {event.run_id} started inside run {open_run}")
            open_run = event.run_id
        elif event.type == "run_end":
            if event.run_id != open_run:
                wrong.append(f"run_end of {event.run_id} while {open_run} was open")
            open_run = None
    if open_run is not None:
        wrong.append(f"run {open_run} has no run_end")
    return wrong


def _calls(events: Sequence[Event], started: Sequence[tuple[str, str]]) -> list[str]:
    wrong: list[str] = []
    calls = _whole_calls(events)
    intents = Tally(
        str(e.payload["call_id"]) for e in events if e.type == "tool_intent"
    )
    classes = {
        str(e.payload["call_id"]): e.payload.get("action_class")
        for e in events
        if e.type == "tool_intent"
    }
    results = Tally(
        str(e.payload["call_id"]) for e in events if e.type == "tool_result"
    )
    answers = {
        str(e.payload["call_id"]): e.payload.get("harness")
        for e in events
        if e.type == "tool_result"
    }
    for call_id in calls:
        if results[call_id] != 1:
            wrong.append(f"call {call_id} has {results[call_id]} results")
    may_start: Tally[tuple[str, str]] = Tally()
    for call_id, count in intents.items():
        # A read may be run once more after a kill, behind a second intent.
        if count > (2 if classes[call_id] == "read" else 1):
            wrong.append(f"call {call_id} has {count} intents")
        harness = answers.get(call_id)
        if harness is not None and harness not in INTERRUPTED_ANSWERS:
            wrong.append(f"call {call_id} had an intent and was answered {harness}")
        # A call answered not_run was not started after its newest intent.
        may_start[calls[call_id]] += count - (1 if harness == "not_run" else 0)
    for key, count in Tally(started).items():
        if count > may_start[key]:
            wrong.append(f"{key} started {count} times for {may_start[key]} intents")
    return wrong


def _whole_calls(events: Sequence[Event]) -> dict[str, tuple[str, str]]:
    """Give each call of a whole reply, by id, with its name and arguments."""
    calls: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.type == "model_response" and _is_whole(event):
            for part in _parts(event):
                if part.get("kind") == "tool_call":
                    arguments = part.get("arguments")
                    calls[str(part["id"])] = (
                        str(part["name"]),
                        canonical_json(arguments)
                        if isinstance(arguments, dict)
                        else "",
                    )
    return calls


def _is_whole(event: Event) -> bool:
    end = event.payload.get("end")
    return isinstance(end, dict) and end.get("kind") == "stop"


def _parts(event: Event) -> list[dict[str, JsonValue]]:
    message = event.payload.get("message")
    assert isinstance(message, dict)
    parts = message.get("parts")
    assert isinstance(parts, list)
    return [part for part in parts if isinstance(part, dict)]


def _messages(ended: Outcome, messages: int) -> list[str]:
    wrong: list[str] = []
    if ended.pending:
        wrong.append(f"inbox rows left: {ended.pending}")
    said = Tally(e.event_id for e in ended.events if e.type == "user_message")
    for event_id, count in said.items():
        if count != 1:
            wrong.append(f"message {event_id} is in the log {count} times")
    if len(said) != messages:
        wrong.append(f"{len(said)} messages in the log, {messages} were sent")
    return wrong


def _replies(ended: Outcome) -> list[str]:
    wrong: list[str] = []
    ready = [e for e in ended.events if e.type == "reply_ready"]
    firsts: list[str] = []
    seen: set[str] = set()
    for out in ended.sent:
        if out.out_id in seen:
            if not out.maybe_duplicate:
                wrong.append(f"a second copy of {out.out_id} is not labelled")
        else:
            seen.add(out.out_id)
            firsts.append(out.text)
    texts = [str(e.payload["text"]) for e in ready]
    if firsts != texts:
        wrong.append(f"sent {firsts}, the log has {texts}")
    return wrong


def with_limits(**limits: int) -> RunSpec:
    """Give the test spec with some of its limits changed."""
    return replace(SPEC, **limits)  # type: ignore[arg-type]


def call(call_id: str, name: str, **arguments: object) -> ToolCallPart:
    """Give a tool call with these arguments."""
    return ToolCallPart(id=call_id, name=name, arguments=dict(arguments))  # type: ignore[arg-type]


class ArrivingTools:
    """Tools that change something, with the scenario's arrival as a tool starts."""

    def __init__(self, inner: ToolProvider, arrival: Arrival) -> None:
        self._inner = inner
        self._arrival = arrival

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        await self._arrival.now(name)
        return await self._inner.call(name, arguments)

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        return await self._inner.plan(name, arguments)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        await self._arrival.now(name)
        return await self._inner.apply(name, arguments, plan)

    async def check(self, probe: JsonObject) -> bool | None:
        return await self._inner.check(probe)


class MirroredLedger:
    """A ledger that keeps a copy of each row it was told of, so a test can see all.

    The port lists only the rows that are still open. The copy is kept in
    the world, so it outlives each process as the ledger does.
    """

    def __init__(self, inner: EffectLedger, rows: dict[int, Effect]) -> None:
        self._inner = inner
        self._rows = rows

    async def begin(
        self, action: object, probe: JsonObject | None, undo: JsonObject | None
    ) -> Begun | Failure:
        begun = await self._inner.begin(action, probe, undo)  # type: ignore[arg-type]
        if isinstance(begun, Begun):
            self._rows[begun.effect.effect_id] = begun.effect
        return begun

    async def finish(self, effect_id: int, result: EffectResult) -> None | Failure:
        finished = await self._inner.finish(effect_id, result)
        if finished is None:
            row = self._rows[effect_id]
            self._rows[effect_id] = replace(row, state=result.outcome, result=result)
        return finished

    async def resolve(self, effect_id: int, outcome: Settled) -> None | Failure:
        resolved = await self._inner.resolve(effect_id, outcome)
        if resolved is None:
            row = self._rows[effect_id]
            self._rows[effect_id] = replace(row, state="resolved", resolution=outcome)
        return resolved

    async def unresolved(self) -> tuple[Effect, ...] | Failure:
        return await self._inner.unresolved()


def changes_wrong(
    events: Sequence[Event],
    rows: Mapping[int, Effect],
    changed: Sequence[tuple[str, str]],
) -> list[str]:
    """Give each rule about changes that the log, the ledger and the world break.

    `changed` holds each change the world saw, as its tool and arguments.
    """
    calls = _whole_calls(events)
    intents = {
        int(str(e.payload["effect_id"])): str(e.payload["call_id"])
        for e in events
        if e.type == "tool_intent" and "effect_id" in e.payload
    }
    results = {
        str(e.payload["call_id"]): e.payload for e in events if e.type == "tool_result"
    }
    wrong: list[str] = []
    for effect_id, call_id in intents.items():
        row = rows.get(effect_id)
        if row is None:
            wrong.append(f"the intent of {call_id} has no ledger row")
        elif call_id in results and row.state == "intended":
            wrong.append(f"call {call_id} has a result and its row is intended")
    for row in rows.values():
        wrong += _row_wrong(row, events, intents, results)
    return wrong + _world_wrong(rows, calls, intents, results, changed)


def _row_wrong(
    row: Effect,
    events: Sequence[Event],
    intents: Mapping[int, str],
    results: Mapping[str, JsonObject],
) -> list[str]:
    wrong: list[str] = []
    named = f"row {row.effect_id} of {row.action.tool}"
    if row.state == "intended":
        wrong.append(f"{named} is still intended")
    if row.effect_id not in intents and row.state != "failed":
        wrong.append(f"{named} has no intent and is {row.state}")
    if row.state == "unknown" and row.action.tool not in MAY_STAY_UNKNOWN:
        wrong.append(f"{named} is left unknown")
    if row.state in ("done", "failed"):
        call_id = intents.get(row.effect_id) or _call_id_at(
            events, row.action.reply_seq, row.action.call_index
        )
        payload = results.get(call_id)
        output = row.result.output if row.result is not None else None
        if payload is None or saved_json(output) != saved_json(payload):
            wrong.append(f"{named} saved another output than its result")
    return wrong


def _world_wrong(
    rows: Mapping[int, Effect],
    calls: Mapping[str, tuple[str, str]],
    intents: Mapping[int, str],
    results: Mapping[str, JsonObject],
    changes: Sequence[tuple[str, str]],
) -> list[str]:
    # A change is known by its tool and arguments. Each row changes the
    # world at most once, so of the rows with one key, no more say it
    # happened than there were changes, and no more say it did not than
    # there were rows that changed nothing. The rows and the texts are
    # counted apart, because each is told to someone.
    changed = Tally(changes)
    begun = Tally(_key(row) for row in rows.values())
    started = Tally(calls[call_id] for call_id in intents.values())
    by_row, by_text = _claims(rows, intents, results)
    wrong: list[str] = []
    for key in set(changed) | set(begun):
        if changed[key] > started[key]:
            wrong.append(
                f"{key} changed {changed[key]} times for {started[key]} intents"
            )
        for who, said in (("a row", by_row), ("a text", by_text)):
            if said[True][key] > changed[key]:
                wrong.append(f"{who} says {key} happened more often than it did")
            if said[False][key] > begun[key] - changed[key]:
                wrong.append(f"{who} says {key} did not happen, and it did")
    return wrong


type Claims = dict[bool, Tally[tuple[str, str]]]


def _claims(
    rows: Mapping[int, Effect],
    intents: Mapping[int, str],
    results: Mapping[str, JsonObject],
) -> tuple[Claims, Claims]:
    """Count, by key, the rows and the texts that say a change did or did not happen."""
    by_row: Claims = {True: Tally(), False: Tally()}
    by_text: Claims = {True: Tally(), False: Tally()}
    for row in rows.values():
        if row.state == "resolved" and row.resolution is not None:
            by_row[row.resolution.happened][_key(row)] += 1
        elif row.state in ("done", "failed"):
            by_row[row.state == "done"][_key(row)] += 1
        result = results.get(intents.get(row.effect_id, ""))
        text = str(result.get("text", "")) if result is not None else ""
        for sentence, happened in ((TOOK_EFFECT, True), (NO_EFFECT, False)):
            if sentence in text:
                by_text[happened][_key(row)] += 1
    return by_row, by_text


def _key(row: Effect) -> tuple[str, str]:
    return (row.action.tool, canonical_json(row.action.args))


def _call_id_at(events: Sequence[Event], reply_seq: int, call_index: int) -> str:
    (reply,) = [e for e in events if e.seq == reply_seq]
    found = [part for part in _parts(reply) if part.get("kind") == "tool_call"]
    return str(found[call_index]["id"])
