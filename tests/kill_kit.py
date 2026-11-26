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

from lamplighter.adapters import (
    CountingIds,
    ScriptedBreak,
    ScriptedChannel,
    ScriptedReply,
    ScriptedTool,
    ScriptedTools,
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
from lamplighter.ports import (
    Event,
    Failure,
    Inbound,
    JsonObject,
    JsonValue,
    ModelRequest,
    TextPart,
    ToolCallPart,
    ToolResult,
    ToolSpec,
    canonical_json,
)
from tests.chat_kit import Outcome, Stores, outcome
from tests.model_by_request import Choose, ModelByRequest, check_request
from tests.port_counter import (
    CountedChannel,
    CountedInbox,
    CountedLog,
    CountedModel,
    CountedOutbox,
    CountedTools,
    Counter,
    When,
)
from tests.run_history import SESSION, SPEC
from tests.waiting_clock import JumpingClock

type Point = tuple[int, When]
type Turn = ScriptedReply | ScriptedBreak

HARNESS_NOTE = "Note from the harness"
EMPTY_NOTE = "your last reply was empty"
INTERRUPTED_ANSWERS = {"interrupted", "stopped", "out_of_time", "not_run"}


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
    order. `during_wait` arrives while the `wait` tool runs. `spec_after`
    is the spec of every process after the first, as when the settings
    change at a restart.
    """

    name: str
    waiting: tuple[Inbound, ...]
    script: dict[str, tuple[Turn, ...]]
    spec: RunSpec = SPEC
    during_wait: Inbound | None = None
    spec_after: RunSpec | None = None


@dataclass
class World:
    """What outlives every process."""

    stores: Stores = field(default_factory=Stores)
    channel: ScriptedChannel = field(
        default_factory=lambda: ScriptedChannel([], JumpingClock())
    )
    clock: JumpingClock = field(default_factory=JumpingClock)
    ids: CountingIds = field(default_factory=CountingIds)
    started: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    processes: int = 0


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
        return ToolResult(text=f"Noted {arguments.get('n')}.", is_error=False)

    ticks = [0]

    async def tick(arguments: JsonObject) -> ToolResult:
        world.started.append(("tick", canonical_json(arguments)))
        ticks[0] += 1
        return ToolResult(
            text=f"Tick {ticks[0]} of process {world.processes}.", is_error=False
        )

    async def wait(arguments: JsonObject) -> ToolResult:
        world.started.append(("wait", canonical_json(arguments)))
        await arrive.now()
        seconds = arguments.get("seconds")
        assert isinstance(seconds, int)
        await world.clock.sleep(seconds)
        return ToolResult(text=f"Waited {seconds} seconds.", is_error=False)

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

    async def now(self) -> None:
        if self.made is not None:
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
) -> None:
    async def kill(number: int, when: When, name: str) -> None:
        del name
        if (number, when) == kill_at:
            raise Killed

    counter.hook = kill
    running = RunningSessions()
    wake = Wake()
    wake.end_input()
    arrival = Arrival(world, scenario.during_wait, running, wake)
    stores = world.stores
    deps = Deps(
        log=CountedLog(stores.log, counter),
        inbox=CountedInbox(stores.inbox, counter),
        outbox=CountedOutbox(stores.outbox, counter),
        model=CountedModel(ModelByRequest(answers(scenario.script)), counter),
        tools=CountedTools(_tools(world, arrival), counter),
        clock=world.clock,
        ids=world.ids,
        observers=Observers(),
        running=running,
    )
    channel = CountedChannel(world.channel, counter)
    assert await drive(SESSION, spec, deps, channel, wake, lambda: None) is None


def run_process(
    world: World, scenario: Scenario, kill_at: Point | None = None
) -> Counter:
    """Run one process on the world to its end, or to the kill. Give its count."""
    spec = (
        scenario.spec
        if world.processes == 0
        else (scenario.spec_after or scenario.spec)
    )
    world.processes += 1
    counter = Counter()
    with contextlib.suppress(Killed):
        asyncio.run(_one_process(world, scenario, spec, kill_at, counter))
    return counter


def new_world(scenario: Scenario) -> World:
    """Give a world with the scenario's messages waiting in the inbox."""
    world = World()

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
    during = scenario.during_wait
    if during is not None and during.kind == "message":
        # It arrives when the wait tool first starts, if it ever does.
        messages += any(name == "wait" for name, _ in world.started)
    stores = ended(world)
    return broken_rules(stores, world.started, messages) + _replies(stores)


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
    for call_id, count in intents.items():
        if count > 1:
            wrong.append(f"call {call_id} has {count} intents")
        harness = answers.get(call_id)
        if harness is not None and harness not in INTERRUPTED_ANSWERS:
            wrong.append(f"call {call_id} had an intent and was answered {harness}")
    may_start = Tally(
        calls[call_id] for call_id in intents if answers.get(call_id) != "not_run"
    )
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
