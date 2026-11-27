"""The promises of the tool port, checked on every provider with the same tests."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lamplighter.adapters import (
    MadeUpWorld,
    ScriptedTool,
    ScriptedTools,
    WorldTools,
)
from lamplighter.core.arguments_check import unsupported
from lamplighter.ports import (
    Failure,
    JsonObject,
    Plan,
    ToolProvider,
    ToolResult,
    ToolSpec,
)
from tests.builtin_kit import tools_on
from tests.stub_tools import StubTools
from tests.waiting_clock import WaitingClock, settle

type Call = tuple[str, JsonObject]


@dataclass
class Made:
    """A provider on a world of its own, and how to look at that world.

    `snapshot` gives what a change would alter. `elsewhere` is a provider of
    the same kind that works in another place. `hold` makes the next apply
    wait until the clock moves on by the change's `hold_s`, for a provider
    whose changes do not wait on the clock by themselves.
    """

    tools: ToolProvider
    snapshot: Callable[[], object]
    clock: WaitingClock
    elsewhere: ToolProvider | None = None
    disturb: Callable[[], None] = lambda: None
    hold: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class Change:
    """A change the suite plans and applies.

    `call` changes the world. `held` is a change whose apply does not end
    until the clock is moved on by `hold_s`. `stubborn` is a change that is
    cancelled while it works, and must still answer truly.
    """

    call: Call
    held: Call
    hold_s: float
    stubborn: Call


@dataclass(frozen=True)
class ToolKind:
    """A provider, and the calls the suite makes on it."""

    make: Callable[[Path, WaitingClock], Made]
    read: Call
    bad: Call
    slow: Call | None = None
    change: Change | None = None
    taints: tuple[str, ...] = field(default=())


def _echo_spec(name: str) -> ToolSpec:
    schema: JsonObject = {"type": "object", "properties": {"text": {"type": "string"}}}
    return ToolSpec(name=name, description=f"The {name} tool.", arguments_schema=schema)


def _scripted(folder: Path, clock: WaitingClock) -> Made:
    del folder

    async def nap(arguments: JsonObject) -> ToolResult:
        del arguments
        await clock.sleep(60)
        return ToolResult(text="rested", is_error=False, effect="none")

    async def echo(arguments: JsonObject) -> ToolResult:
        text = arguments.get("text")
        if not isinstance(text, str):
            return ToolResult(text="echo needs text.", is_error=True, effect="none")
        return ToolResult(text=text, is_error=False, effect="none")

    tools = ScriptedTools(
        [
            ScriptedTool(_echo_spec("nap"), nap),
            ScriptedTool(_echo_spec("echo"), echo, taints=True),
        ]
    )
    return Made(tools, lambda: None, clock)


def _stub(folder: Path, clock: WaitingClock) -> Made:
    del folder
    return Made(StubTools(clock), lambda: None, clock)


def _world(folder: Path, clock: WaitingClock) -> Made:
    del folder
    world = MadeUpWorld(texts={"tea": "green"})
    other = MadeUpWorld(name="world-2", texts={"tea": "green"})

    def snapshot() -> object:
        return (dict(world.texts), list(world.launched))

    def disturb() -> None:
        world.texts["tea"] = "black, put by hand"

    return Made(
        WorldTools(world, clock),
        snapshot,
        clock,
        elsewhere=WorldTools(other, clock),
        disturb=disturb,
    )


HOLD_S = 5


def _builtin(folder: Path, clock: WaitingClock) -> Made:
    work, other = folder / "work", folder / "other"
    for made in (work, other):
        made.mkdir()
        (made / "tea.txt").write_text("green\n")

    def snapshot() -> object:
        return {
            path.relative_to(work).as_posix(): path.read_bytes()
            for path in sorted(work.rglob("*"))
            if path.is_file()
        }

    def disturb() -> None:
        (work / "tea.txt").write_text("black, put by hand\n")

    kept: list[asyncio.Future[None]] = []

    def hold() -> None:
        # The work of a file tool runs in a worker thread. With the only
        # worker busy until the clock moves on, the apply waits too.
        release = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(pool)
        # Bounded, so a failing test ends instead of waiting for the worker.
        pool.submit(release.wait, 10)

        async def let_go() -> None:
            await clock.sleep(HOLD_S)
            release.set()

        kept.append(asyncio.ensure_future(let_go()))

    return Made(
        tools_on(work.resolve()),
        snapshot,
        clock,
        elsewhere=tools_on(other.resolve()),
        disturb=disturb,
        hold=hold,
    )


KINDS = {
    "scripted": ToolKind(
        make=_scripted,
        read=("echo", {"text": "tea"}),
        bad=("echo", {"text": 7}),
        slow=("nap", {}),
        taints=("echo",),
    ),
    "stub": ToolKind(
        make=_stub,
        read=("current_time", {}),
        bad=("wait", {"seconds": "soon"}),
        slow=("wait", {"seconds": 600}),
    ),
    "made-up world": ToolKind(
        make=_world,
        read=("note", {"n": 1}),
        bad=("big", {"size": 0}),
        change=Change(
            call=("put", {"name": "tea", "text": "mint"}),
            held=("slow_put", {"name": "tea", "text": "mint"}),
            hold_s=5,
            stubborn=("stubborn_put", {"name": "tea", "text": "mint"}),
        ),
        taints=("page",),
    ),
    "built-in": ToolKind(
        make=_builtin,
        read=("read_file", {"path": "tea.txt"}),
        bad=("read_file", {"path": 7}),
        taints=("read_output", "web_fetch"),
        change=Change(
            call=("write_file", {"path": "tea.txt", "content": "mint\n"}),
            held=("write_file", {"path": "tea.txt", "content": "mint\n"}),
            hold_s=HOLD_S,
            stubborn=(
                "edit_file",
                {"path": "tea.txt", "old_text": "green", "new_text": "mint"},
            ),
        ),
    ),
}
WITH_CHANGES = {name: kind for name, kind in KINDS.items() if kind.change}
SLOW_READS = {name: kind for name, kind in KINDS.items() if kind.slow}


@pytest.fixture(params=list(KINDS.values()), ids=list(KINDS))
def kind(request: pytest.FixtureRequest) -> ToolKind:
    return request.param


@pytest.fixture(params=list(WITH_CHANGES.values()), ids=list(WITH_CHANGES))
def changing(request: pytest.FixtureRequest) -> ToolKind:
    return request.param


def _change(kind: ToolKind) -> Change:
    assert kind.change is not None
    return kind.change


async def _planned(made: Made, change: Call) -> Plan:
    plan = await made.tools.plan(*change)
    assert isinstance(plan, Plan), plan
    return plan


def test_the_list_is_the_same_twice_and_sorted_by_name(
    kind: ToolKind, tmp_path: Path
) -> None:
    tools = kind.make(tmp_path, WaitingClock()).tools
    first = tools.list_tools()
    assert first == tools.list_tools()
    names = [entry.spec.name for entry in first]
    assert names == sorted(names)
    assert len(set(names)) == len(names)


def test_every_schema_uses_only_what_the_check_knows(
    kind: ToolKind, tmp_path: Path
) -> None:
    tools = kind.make(tmp_path, WaitingClock()).tools
    for entry in tools.list_tools():
        assert unsupported(entry.spec.arguments_schema) == (), entry.spec.name


def test_exactly_the_tools_whose_text_is_from_outside_taint(
    kind: ToolKind, tmp_path: Path
) -> None:
    tools = kind.make(tmp_path, WaitingClock()).tools
    tainting = {entry.spec.name for entry in tools.list_tools() if entry.taints}
    assert tainting == set(kind.taints)


def test_a_read_run_twice_leaves_the_world_and_gives_the_same_text(
    kind: ToolKind, tmp_path: Path
) -> None:
    made = kind.make(tmp_path, WaitingClock())
    before = made.snapshot()
    first = asyncio.run(made.tools.call(*kind.read))
    second = asyncio.run(made.tools.call(*kind.read))
    assert isinstance(first, ToolResult), first
    assert first == second
    assert not first.is_error
    assert first.effect == "none"
    assert made.snapshot() == before


def test_bad_arguments_give_an_error_result_and_do_not_raise(
    kind: ToolKind, tmp_path: Path
) -> None:
    result = asyncio.run(kind.make(tmp_path, WaitingClock()).tools.call(*kind.bad))
    assert isinstance(result, ToolResult), result
    assert result.is_error
    assert result.effect == "none"


@pytest.mark.parametrize("slow", list(SLOW_READS.values()), ids=list(SLOW_READS))
def test_a_read_can_be_cancelled_while_it_runs(slow: ToolKind, tmp_path: Path) -> None:
    async def scene() -> None:
        made = slow.make(tmp_path, WaitingClock())
        assert slow.slow is not None
        task = asyncio.create_task(made.tools.call(*slow.slow))
        await settle()
        assert not task.done()
        task.cancel()
        await settle()
        assert task.cancelled()

    asyncio.run(scene())


def test_an_unlisted_name_is_a_failure_and_not_a_raise(
    kind: ToolKind, tmp_path: Path
) -> None:
    result = asyncio.run(kind.make(tmp_path, WaitingClock()).tools.call("nothing", {}))
    assert isinstance(result, Failure)
    assert result.kind == "invalid_input"


def test_a_plan_changes_nothing_and_names_the_same_target_twice(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    before = made.snapshot()
    first = asyncio.run(_planned(made, _change(changing).call))
    second = asyncio.run(_planned(made, _change(changing).call))
    assert first.target == second.target
    assert made.snapshot() == before


def test_apply_changes_the_world_and_says_done(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    before = made.snapshot()
    change = _change(changing).call

    async def scene() -> ToolResult | Failure:
        return await made.tools.apply(*change, await _planned(made, change))

    result = asyncio.run(scene())
    assert isinstance(result, ToolResult), result
    assert result.effect == "done"
    assert made.snapshot() != before


def test_apply_after_the_world_changed_refuses_and_changes_nothing(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    change = _change(changing).call

    async def scene() -> tuple[ToolResult | Failure, object]:
        plan = await _planned(made, change)
        made.disturb()
        disturbed = made.snapshot()
        return await made.tools.apply(*change, plan), disturbed

    result, disturbed = asyncio.run(scene())
    assert isinstance(result, ToolResult), result
    assert result.is_error
    assert result.effect == "none"
    assert made.snapshot() == disturbed


def test_check_says_false_before_the_apply_and_true_after(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    change = _change(changing).call

    async def scene() -> tuple[bool | None, bool | None]:
        plan = await _planned(made, change)
        assert plan.probe is not None
        before = await made.tools.check(plan.probe)
        await made.tools.apply(*change, plan)
        return before, await made.tools.check(plan.probe)

    assert asyncio.run(scene()) == (False, True)


def test_check_cannot_tell_for_a_probe_of_an_unknown_kind(
    kind: ToolKind, tmp_path: Path
) -> None:
    tools = kind.make(tmp_path, WaitingClock()).tools
    probe: JsonObject = {"kind": "no_such_probe", "v": 1}
    assert asyncio.run(tools.check(probe)) is None


def test_check_cannot_tell_for_a_probe_made_in_another_place(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    assert made.elsewhere is not None
    plan = asyncio.run(_planned(made, _change(changing).call))
    assert plan.probe is not None
    assert asyncio.run(made.elsewhere.check(plan.probe)) is None


def test_apply_of_a_plan_made_in_another_place_refuses_and_changes_nothing(
    changing: ToolKind, tmp_path: Path
) -> None:
    made = changing.make(tmp_path, WaitingClock())
    assert made.elsewhere is not None
    change = _change(changing).call
    before = made.snapshot()
    plan = asyncio.run(_planned(made, change))
    result = asyncio.run(made.elsewhere.apply(*change, plan))
    assert result.effect == "none"
    assert isinstance(result, Failure) or result.is_error
    assert made.snapshot() == before


def test_check_cannot_tell_while_the_apply_of_its_change_is_at_work(
    changing: ToolKind, tmp_path: Path
) -> None:
    change = _change(changing)

    async def scene() -> tuple[bool | None, bool | None]:
        made = changing.make(tmp_path, WaitingClock())
        plan = await _planned(made, change.held)
        assert plan.probe is not None
        made.hold()
        task = asyncio.create_task(made.tools.apply(*change.held, plan))
        await settle()
        assert not task.done()
        during = await made.tools.check(plan.probe)
        await made.clock.advance(change.hold_s)
        await task
        return during, await made.tools.check(plan.probe)

    assert asyncio.run(scene()) == (None, True)


def test_a_cancelled_apply_that_goes_on_still_answers_truly(
    changing: ToolKind, tmp_path: Path
) -> None:
    change = _change(changing)

    async def scene() -> None:
        made = changing.make(tmp_path, WaitingClock())
        before = made.snapshot()
        plan = await _planned(made, change.stubborn)
        made.hold()
        task = asyncio.create_task(made.tools.apply(*change.stubborn, plan))
        await settle()
        task.cancel()
        await made.clock.advance(change.hold_s)
        result = await task
        assert isinstance(result, ToolResult), result
        assert result.effect == "done"
        assert made.snapshot() != before

    asyncio.run(scene())
