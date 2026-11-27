"""The made-up world's tools: what each plans, applies and notes."""

import asyncio

from lamplighter.adapters import MadeUpWorld, WorldTools
from lamplighter.adapters.made_up_world import PAGE_TEXT, text_hash
from lamplighter.ports import Failure, Plan, ToolResult
from tests.waiting_clock import WaitingClock


def tools_on(world: MadeUpWorld) -> WorldTools:
    return WorldTools(world, WaitingClock())


def test_a_put_plans_a_probe_and_a_saved_copy_then_writes() -> None:
    world = MadeUpWorld(texts={"tea": "green"})
    tools = tools_on(world)
    plan = asyncio.run(tools.plan("put", {"name": "tea", "text": "mint"}))
    assert plan == Plan(
        target="tea",
        probe={
            "kind": "text_hash",
            "v": 1,
            "world": "world-1",
            "name": "tea",
            "before": text_hash("green"),
            "after": text_hash("mint"),
        },
        undo={
            "kind": "restore_text",
            "v": 1,
            "world": "world-1",
            "name": "tea",
            "text": "green",
        },
    )
    assert isinstance(plan, Plan)
    result = asyncio.run(tools.apply("put", {"name": "tea", "text": "mint"}, plan))
    assert result == ToolResult("Put tea.", False, "done")
    assert world.texts == {"tea": "mint"}
    assert world.started == [("put", '{"name":"tea","text":"mint"}')]
    assert world.changed == [("put", '{"name":"tea","text":"mint"}')]


def test_a_plan_for_other_arguments_or_another_world_is_refused_unwritten() -> None:
    world = MadeUpWorld(texts={"tea": "green"})
    plan = asyncio.run(tools_on(world).plan("put", {"name": "tea", "text": "mint"}))
    assert isinstance(plan, Plan)
    other = WorldTools(
        MadeUpWorld(name="world-2", texts={"tea": "green"}), WaitingClock()
    )
    tries = (
        (tools_on(world), {"text": "mint"}),
        (tools_on(world), {"name": "tea", "text": "lime"}),
        (tools_on(world), {"name": "tea"}),
        (other, {"name": "tea", "text": "mint"}),
    )
    for tools, arguments in tries:
        result = asyncio.run(tools.apply("put", arguments, plan))
        assert isinstance(result, Failure) and result.kind == "invalid_input"
    assert world.texts == {"tea": "green"}
    assert world.changed == []


def test_a_put_of_the_same_text_plans_nothing() -> None:
    tools = tools_on(MadeUpWorld(texts={"tea": "green"}))
    planned = asyncio.run(tools.plan("put", {"name": "tea", "text": "green"}))
    assert planned == ToolResult("tea already holds this text.", False, "none")


def test_a_locked_text_is_refused_in_the_plan() -> None:
    tools = tools_on(MadeUpWorld())
    planned = asyncio.run(tools.plan("put", {"name": "locked-a", "text": "x"}))
    assert planned == ToolResult("The text locked-a is locked.", True, "none")


def test_a_launch_has_no_probe_and_no_undo() -> None:
    world = MadeUpWorld()
    tools = tools_on(world)
    plan = asyncio.run(tools.plan("launch", {"what": "rocket"}))
    assert plan == Plan(target="rocket", probe=None, undo=None)
    assert isinstance(plan, Plan)
    asyncio.run(tools.apply("launch", {"what": "rocket"}, plan))
    assert world.launched == ["rocket"]


def test_a_shaky_put_cannot_say_whether_it_wrote() -> None:
    for lands in (True, False):
        world = MadeUpWorld()
        tools = tools_on(world)
        arguments = {"name": "tea", "text": "mint", "lands": lands}
        plan = asyncio.run(tools.plan("shaky_put", arguments))
        assert isinstance(plan, Plan)
        failed = asyncio.run(tools.apply("shaky_put", arguments, plan))
        assert isinstance(failed, Failure)
        assert failed.effect == "unknown"
        assert ("tea" in world.texts) is lands
        assert plan.probe is not None
        assert asyncio.run(tools.check(plan.probe)) is lands


def test_a_page_is_outside_text_with_an_instruction_in_it() -> None:
    tools = tools_on(MadeUpWorld())
    entries = {entry.spec.name: entry for entry in tools.list_tools()}
    assert entries["page"].taints
    result = asyncio.run(tools.call("page", {}))
    assert result == ToolResult(PAGE_TEXT, False, "none")


def test_big_gives_a_text_of_the_size_asked() -> None:
    result = asyncio.run(tools_on(MadeUpWorld()).call("big", {"size": 31_000}))
    assert isinstance(result, ToolResult)
    assert len(result.text) == 31_000


def test_a_check_cannot_tell_a_third_text() -> None:
    world = MadeUpWorld(texts={"tea": "green"})
    tools = tools_on(world)
    plan = asyncio.run(tools.plan("put", {"name": "tea", "text": "mint"}))
    assert isinstance(plan, Plan) and plan.probe is not None
    world.texts["tea"] = "black"
    assert asyncio.run(tools.check(plan.probe)) is None
