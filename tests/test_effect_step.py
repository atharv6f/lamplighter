"""A change through the ledger: its events, its row, and each way it can be cut off."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest

from lamplighter.adapters import MadeUpWorld, ScriptedReply, WorldTools
from lamplighter.adapters.in_memory import InMemoryEffectLedger
from lamplighter.adapters.made_up_world import text_hash
from lamplighter.core import TurnEnd, harness_texts, run_turn
from lamplighter.core.action import action_hash
from lamplighter.core.run_spec import RunSpec
from lamplighter.ports import (
    Effect,
    EffectResult,
    Event,
    Failure,
    JsonObject,
    Plan,
    TextPart,
    ToolCallPart,
    ToolEntry,
    ToolResult,
    Usage,
    effect_key,
    event_id_for,
)
from tests.kill_kit import MirroredLedger
from tests.loop_kit import Loop, in_memory, in_order, stop_now
from tests.model_by_request import ModelByRequest
from tests.run_history import SESSION, SPEC, inbound
from tests.test_observers import Seen, recording
from tests.waiting_clock import JumpingClock

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
DONE = ScriptedReply((TextPart(text="Done."),), "end", USAGE)


def asks(*calls: ToolCallPart) -> ScriptedReply:
    return ScriptedReply(calls, "tool_calls", USAGE)


def put(name: str, text: str, tool: str = "put", call_id: str = "c1") -> ToolCallPart:
    return ToolCallPart(id=call_id, name=tool, arguments={"name": name, "text": text})


class Hooked:
    """The made-up world's tools, with a hook that runs as an apply starts."""

    def __init__(self, inner: WorldTools) -> None:
        self.inner = inner
        self.on_apply: Callable[[], Awaitable[None]] | None = None
        self.on_plan: Callable[[], Awaitable[None]] | None = None
        self.after_apply: (
            Callable[[ToolResult | Failure], ToolResult | Failure] | None
        ) = None
        self.checks = 0
        self.entries: tuple[ToolEntry, ...] | None = None

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self.entries or self.inner.list_tools()

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
        if self.on_apply is not None:
            await self.on_apply()
        applied = await self.inner.apply(name, arguments, plan)
        return applied if self.after_apply is None else self.after_apply(applied)

    async def check(self, probe: JsonObject) -> bool | None:
        self.checks += 1
        return await self.inner.check(probe)


@dataclass
class Scene:
    """A loop on the made-up world, and a copy of every ledger row it wrote."""

    loop: Loop
    world: MadeUpWorld
    tools: Hooked
    rows: dict[int, Effect]

    async def turn(self, spec: RunSpec = SPEC) -> TurnEnd | Failure | None:
        return await run_turn(SESSION, spec, self.loop.deps)

    async def events(self) -> tuple[Event, ...]:
        return await self.loop.events(SESSION)

    @property
    def row(self) -> Effect:
        (only,) = self.rows.values()
        return only


def scene_of(*replies: ScriptedReply, texts: dict[str, str] | None = None) -> Scene:
    clock = JumpingClock()
    world = MadeUpWorld(texts=dict(texts or {}))
    tools = Hooked(WorldTools(world, clock))
    loop = in_memory(ModelByRequest(in_order(*replies, DONE)), clock, tools)
    rows: dict[int, Effect] = {}
    loop.ledger = MirroredLedger(loop.ledger, rows)  # type: ignore[assignment]
    return Scene(loop, world, tools, rows)


def played(
    scene: Scene, body: Callable[[Scene], Awaitable[None]] | None = None
) -> tuple[Event, ...]:
    """Ask, run the body or one turn, and give the events. The tools end with it."""

    async def run() -> tuple[Event, ...]:
        try:
            await scene.loop.inbox.accept(inbound("m1", "Change it."))
            if body is None:
                ended = await scene.turn()
                assert isinstance(ended, TurnEnd), ended
            else:
                await body(scene)
            return await scene.events()
        finally:
            scene.tools.inner.gone()

    return asyncio.run(run())


def of(events: Sequence[Event], kind: str) -> list[Event]:
    return [e for e in events if e.type == kind]


def result_of(events: Sequence[Event]) -> dict[str, Any]:
    (result,) = of(events, "tool_result")
    return dict(result.payload)


def test_one_change_saves_these_events_and_this_row() -> None:
    scene = scene_of(asks(put("tea", "mint")), texts={"tea": "green"})
    events = played(scene)
    reply = of(events, "model_response")[0]
    (intent,) = of(events, "tool_intent")
    (result,) = of(events, "tool_result")
    assert intent.payload == {
        "call_id": "c1",
        "name": "put",
        "action_class": "reversible_write",
        "target": "tea",
        "effect_id": 1,
    }
    assert intent.cause == reply.event_id
    payload = {"call_id": "c1", "text": "Put tea.", "is_error": False}
    assert result.payload == payload
    assert result.cause == event_id_for(SESSION, intent.seq)
    row = scene.row
    assert row.state == "done"
    assert row.result == EffectResult("done", payload)
    arguments = {"name": "tea", "text": "mint"}
    assert row.action.effect_key == effect_key(SESSION, reply.seq, 0, 0)
    assert row.action.action_hash == action_hash("put", arguments, "tea")
    assert (row.action.reply_seq, row.action.call_index, row.action.attempt) == (
        reply.seq,
        0,
        0,
    )
    assert (row.action.args, row.action.target, row.action.outward) == (
        arguments,
        "tea",
        False,
    )
    assert row.probe == {
        "kind": "text_hash",
        "v": 1,
        "world": "world-1",
        "name": "tea",
        "before": text_hash("green"),
        "after": text_hash("mint"),
    }
    assert row.undo == {
        "kind": "restore_text",
        "v": 1,
        "world": "world-1",
        "name": "tea",
        "text": "green",
    }
    assert scene.world.texts == {"tea": "mint"}


def test_a_change_that_changes_nothing_has_one_result_no_intent_and_no_row() -> None:
    scene = scene_of(asks(put("tea", "green")), texts={"tea": "green"})
    events = played(scene)
    assert of(events, "tool_intent") == []
    assert result_of(events) == {
        "call_id": "c1",
        "text": "tea already holds this text.",
        "is_error": False,
    }
    assert scene.rows == {}


def test_a_change_refused_in_its_plan_is_an_error_with_no_row() -> None:
    scene = scene_of(asks(put("locked-a", "x")))
    events = played(scene)
    assert of(events, "tool_intent") == []
    assert result_of(events) == {
        "call_id": "c1",
        "text": "The text locked-a is locked.",
        "is_error": True,
    }
    assert scene.rows == {}


def test_a_change_refused_in_apply_has_a_failed_row_and_the_tools_text() -> None:
    scene = scene_of(asks(put("tea", "mint")), texts={"tea": "green"})

    async def by_hand() -> None:
        scene.world.texts["tea"] = "black"

    scene.tools.on_apply = by_hand
    events = played(scene)
    text = "The text tea changed since the plan. Nothing was written."
    assert result_of(events) == {"call_id": "c1", "text": text, "is_error": True}
    assert scene.row.state == "failed"
    assert scene.world.texts == {"tea": "black"}


def stopping(scene: Scene) -> None:
    async def stop() -> None:
        await stop_now(scene.loop, "s1")

    scene.tools.on_apply = stop


def test_a_stop_during_a_change_resolves_its_row_from_the_check() -> None:
    scene = scene_of(asks(put("tea", "mint", "slow_put")))
    stopping(scene)
    events = played(scene)
    payload = result_of(events)
    assert payload["harness"] == "stopped"
    assert payload["text"] == harness_texts.unsettled("stopped", "slow_put", False)
    assert payload["text"].endswith(harness_texts.NO_EFFECT)
    row = scene.row
    assert row.state == "resolved"
    assert row.result is not None
    assert row.result.output == {"reason": "stopped", "still_running": False}
    assert row.resolution is not None
    assert (row.resolution.happened, row.resolution.settled_by) == (False, "probe")
    assert row.resolution.evidence == row.probe


def test_a_stop_during_a_change_that_ignores_it_leaves_the_row_unknown() -> None:
    scene = scene_of(asks(put("tea", "mint", "stubborn_put")))
    stopping(scene)
    events = played(scene)
    payload = result_of(events)
    assert payload["text"] == (
        f"{harness_texts.stopped('stubborn_put')} {harness_texts.DID_NOT_STOP}"
    )
    assert scene.row.state == "unknown"
    assert scene.row.result is not None
    assert scene.row.result.output == {"reason": "stopped", "still_running": True}
    assert scene.tools.checks == 0


def test_the_time_limit_during_a_change_resolves_its_row_from_the_check() -> None:
    scene = scene_of(asks(put("tea", "mint", "slow_put")))

    async def body(scene: Scene) -> None:
        ended = await scene.turn(replace(SPEC, max_seconds=3))
        assert isinstance(ended, TurnEnd) and ended.reason == "limit"

    payload = result_of(played(scene, body))
    assert payload["harness"] == "out_of_time"
    assert payload["text"] == harness_texts.unsettled("out_of_time", "slow_put", False)
    assert scene.row.state == "resolved"


def test_the_time_limit_during_a_change_that_ignores_it_leaves_it_unknown() -> None:
    scene = scene_of(asks(put("tea", "mint", "stubborn_put")))

    async def body(scene: Scene) -> None:
        ended = await scene.turn(replace(SPEC, max_seconds=3))
        assert isinstance(ended, TurnEnd) and ended.reason == "limit"

    payload = result_of(played(scene, body))
    assert payload["text"] == (
        f"{harness_texts.out_of_time('stubborn_put')} {harness_texts.DID_NOT_STOP}"
    )
    assert scene.row.state == "unknown"
    assert scene.tools.checks == 0


def test_a_failure_that_cannot_say_is_unconfirmed_with_what_the_check_found() -> None:
    for lands in (True, False):
        call = ToolCallPart(
            id="c1",
            name="shaky_put",
            arguments={"name": "tea", "text": "mint", "lands": lands},
        )
        scene = scene_of(asks(call))
        payload = result_of(played(scene))
        failure = (
            "shaky_put failed (transient): The line dropped before the answer came."
        )
        assert payload["harness"] == "unconfirmed"
        assert payload["text"] == (
            f"{failure} It could not say whether anything was changed."
            f" {harness_texts.TOOK_EFFECT if lands else harness_texts.NO_EFFECT}"
        )
        row = scene.row
        assert row.state == "resolved"
        assert row.result is not None
        assert row.result.output == {
            "reason": "unconfirmed",
            "still_running": False,
            "failure": failure,
        }
        assert row.resolution is not None and row.resolution.happened is lands


def test_a_failure_that_says_it_changed_the_world_has_a_done_row() -> None:
    # In the ledger, failed means "did not happen", so this row is done.
    scene = scene_of(asks(put("tea", "mint")))
    lost = Failure(
        kind="unavailable",
        effect="done",
        message="The text was written and the answer was lost.",
        source="t",
    )
    scene.tools.after_apply = lambda _: lost
    payload = result_of(played(scene))
    assert payload["is_error"] is True
    assert payload["text"] == harness_texts.tool_failed("put", lost.kind, lost.message)
    assert scene.row.state == "done"
    assert scene.row.result == EffectResult("done", payload)
    assert scene.world.texts == {"tea": "mint"}


class FailingPuts:
    """A blob store whose every put fails."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    async def put(self, data: bytes, media_type: str) -> object:
        del data, media_type
        return Failure(kind="unavailable", effect="none", message="No.", source="t")


def test_a_failed_blob_save_still_writes_the_cut_result_and_the_row_is_done() -> None:
    scene = scene_of(asks(put("tea", "m" * 40_000)))
    scene.loop.blobs = FailingPuts(scene.loop.blobs)  # type: ignore[assignment]
    payload = result_of(played(scene))
    assert "full_output" not in payload
    assert payload["text"] == "Put tea."
    assert scene.row.state == "done"


def test_two_calls_with_one_id_are_each_run_with_a_row_of_their_own() -> None:
    first, second = put("tea", "mint"), put("jam", "plum")
    scene = scene_of(asks(first, second))
    events = played(scene)
    assert [e.payload["effect_id"] for e in of(events, "tool_intent")] == [1, 2]
    assert [r.action.call_index for r in scene.rows.values()] == [0, 1]
    assert [r.state for r in scene.rows.values()] == ["done", "done"]
    assert scene.world.texts == {"tea": "mint", "jam": "plum"}


def test_a_row_begun_whose_tool_is_then_gone_is_closed_when_the_run_ends() -> None:
    for kept in ("gone", "a read"):
        scene = scene_of(asks(put("tea", "mint")))

        async def body(scene: Scene, kept: str = kept) -> None:
            killed = KillAfterBegin(scene.loop.ledger)
            scene.loop.ledger = killed  # type: ignore[assignment]
            assert isinstance(await scene.turn(), Failure)
            scene.loop.ledger = killed.inner  # type: ignore[assignment]
            entries = scene.tools.inner.list_tools()
            scene.tools.entries = tuple(
                e if e.spec.name != "put" else replace(e, action_class="read")
                for e in entries
                if kept == "a read" or e.spec.name != "put"
            )
            ended = await scene.turn(replace(SPEC, config_id="d" * 64))
            assert isinstance(ended, TurnEnd) and ended.reason == "failed"

        events = played(scene, body)
        payload = result_of(events)
        assert payload["harness"] == "not_run"
        assert scene.row.state == "failed"
        assert scene.row.result == EffectResult("failed", payload)
        assert scene.world.texts == {}


class KillAfterBegin:
    """A ledger that saves the first row, then fails, as a kill after `begin` would."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def begin(self, action: Any, probe: Any, undo: Any) -> Any:
        await self.inner.begin(action, probe, undo)
        return Failure(kind="unavailable", effect="none", message="Gone.", source="t")

    async def unresolved(self) -> Any:
        return await self.inner.unresolved()


def test_the_end_of_a_run_closes_a_row_left_open_with_the_not_run_payload() -> None:
    scene = scene_of(asks(put("tea", "mint")))

    async def body(scene: Scene) -> None:
        killed = KillAfterBegin(scene.loop.ledger)
        scene.loop.ledger = killed  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.ledger = killed.inner  # type: ignore[assignment]
        await stop_now(scene.loop, "s1")
        ended = await scene.turn()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"

    events = played(scene, body)
    payload = result_of(events)
    assert payload == {
        "call_id": "c1",
        "text": harness_texts.not_run("put"),
        "is_error": True,
        "harness": "not_run",
    }
    assert scene.row.state == "failed"
    assert scene.row.result == EffectResult("failed", payload)


def test_a_row_begun_before_a_kill_is_carried_on_with_its_own_recipes() -> None:
    scene = scene_of(asks(put("tea", "mint")), texts={"tea": "green"})

    async def body(scene: Scene) -> None:
        killed = KillAfterBegin(scene.loop.ledger)
        scene.loop.ledger = killed  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.ledger = killed.inner  # type: ignore[assignment]
        # A hand edit while the process is down. The new plan would save it
        # as the old copy, and the row still holds the copy from before.
        scene.world.texts["tea"] = "black"
        assert isinstance(await scene.turn(), TurnEnd)

    events = played(scene, body)
    assert of(events, "tool_intent")[0].payload["effect_id"] == 1
    assert scene.row.state == "failed"
    assert scene.row.undo is not None and scene.row.undo["text"] == "green"
    assert scene.world.texts == {"tea": "black"}


class FinishFailsOnce:
    """A ledger whose first `finish` fails, after the tool has ended."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.failed = False

    async def begin(self, action: Any, probe: Any, undo: Any) -> Any:
        return await self.inner.begin(action, probe, undo)

    async def finish(self, effect_id: int, result: Any) -> Any:
        if not self.failed:
            self.failed = True
            return Failure(kind="unavailable", effect="none", message="No.", source="t")
        return await self.inner.finish(effect_id, result)

    async def resolve(self, effect_id: int, outcome: Any) -> Any:
        return await self.inner.resolve(effect_id, outcome)

    async def unresolved(self) -> Any:
        return await self.inner.unresolved()


def test_a_lost_finish_is_settled_by_the_check_on_the_next_call() -> None:
    scene = scene_of(asks(put("tea", "mint")))

    async def body(scene: Scene) -> None:
        scene.loop.ledger = FinishFailsOnce(scene.loop.ledger)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        assert isinstance(await scene.turn(), TurnEnd)

    payload = result_of(played(scene, body))
    assert payload["harness"] == "interrupted"
    assert payload["text"] == harness_texts.unsettled("interrupted", "put", True)
    assert scene.row.state == "resolved"
    assert scene.world.texts == {"tea": "mint"}


def test_a_lost_finish_of_a_change_with_no_probe_stays_unknown() -> None:
    launch = ToolCallPart(id="c1", name="launch", arguments={"what": "rocket"})
    scene = scene_of(asks(launch))

    async def body(scene: Scene) -> None:
        scene.loop.ledger = FinishFailsOnce(scene.loop.ledger)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        assert isinstance(await scene.turn(), TurnEnd)

    payload = result_of(played(scene, body))
    assert payload["text"] == (
        "The run was cut off before the result of launch was saved, and the call"
        " was not run again. Its outcome is unknown: it may have finished, partly"
        " run, or not run at all."
    )
    assert scene.row.state == "unknown"
    assert scene.world.launched == ["rocket"]


class LosesTheResult:
    """A log that loses the first result of a change, as a kill before it would."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.lost = False

    async def append(self, session_id: str, events: Any, expected_seq: int) -> Any:
        if not self.lost and events[0].type == "tool_result":
            self.lost = True
            return Failure(kind="unavailable", effect="none", message="No.", source="t")
        return await self.inner.append(session_id, events, expected_seq)

    async def read(self, session_id: str, after_seq: int) -> Any:
        return await self.inner.read(session_id, after_seq)


def test_a_row_saved_still_running_is_checked_after_a_restart() -> None:
    scene = scene_of(asks(put("tea", "mint", "stubborn_put")))
    stopping(scene)

    async def body(scene: Scene) -> None:
        log = scene.loop.log
        scene.loop.log = LosesTheResult(log)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        assert scene.row.state == "unknown"
        # A restart: the tool is gone with its process, and a new provider
        # knows of no apply at work.
        scene.tools.inner.gone()
        await asyncio.sleep(0)
        scene.tools.inner = WorldTools(scene.world, scene.loop.clock)
        scene.loop.log = log
        assert isinstance(await scene.turn(), TurnEnd)

    payload = result_of(played(scene, body))
    assert payload["harness"] == "stopped"
    assert payload["text"] == harness_texts.unsettled("stopped", "stubborn_put", False)
    assert scene.row.state == "resolved"


SHAKY_FAILURE = "shaky_put failed (transient): The line dropped before the answer came."


def shaky(lands: bool) -> ToolCallPart:
    arguments: JsonObject = {"name": "tea", "text": "mint", "lands": lands}
    return ToolCallPart(id="c1", name="shaky_put", arguments=arguments)


def unconfirmed_text(lands: bool) -> str:
    found = harness_texts.TOOK_EFFECT if lands else harness_texts.NO_EFFECT
    return f"{SHAKY_FAILURE} It could not say whether anything was changed. {found}"


def test_a_settled_row_whose_result_was_lost_gives_what_the_check_found() -> None:
    for lands in (True, False):
        scene = scene_of(asks(shaky(lands)))

        async def body(scene: Scene) -> None:
            log = scene.loop.log
            scene.loop.log = LosesTheResult(log)  # type: ignore[assignment]
            assert isinstance(await scene.turn(), Failure)
            assert scene.row.state == "resolved"
            scene.loop.log = log
            assert isinstance(await scene.turn(), TurnEnd)

        payload = result_of(played(scene, body))
        assert payload["harness"] == "unconfirmed"
        assert payload["text"] == unconfirmed_text(lands)
        assert len(scene.world.started) == 1


class ResolveFailsOnce(FinishFailsOnce):
    """A ledger whose first `resolve` fails, as a kill before it would."""

    async def finish(self, effect_id: int, result: Any) -> Any:
        return await self.inner.finish(effect_id, result)

    async def resolve(self, effect_id: int, outcome: Any) -> Any:
        if not self.failed:
            self.failed = True
            return Failure(kind="unavailable", effect="none", message="No.", source="t")
        return await self.inner.resolve(effect_id, outcome)


def test_an_unconfirmed_row_left_unknown_is_checked_with_its_failure_later() -> None:
    for lands in (True, False):
        scene = scene_of(asks(shaky(lands)))

        async def body(scene: Scene) -> None:
            scene.loop.ledger = ResolveFailsOnce(scene.loop.ledger)  # type: ignore[assignment]
            assert isinstance(await scene.turn(), Failure)
            assert scene.row.state == "unknown"
            assert isinstance(await scene.turn(), TurnEnd)

        payload = result_of(played(scene, body))
        assert payload["harness"] == "unconfirmed"
        assert payload["text"] == unconfirmed_text(lands)
        assert scene.row.state == "resolved"
        assert len(scene.world.started) == 1


def test_a_change_with_its_intent_saved_is_settled_and_not_applied_again() -> None:
    scene = scene_of(asks(put("tea", "mint")))

    async def body(scene: Scene) -> None:
        log = scene.loop.log
        scene.loop.log = LosesTheResult(log)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.log = log
        await stop_now(scene.loop, "s1")
        ended = await scene.turn()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"

    events = played(scene, body)
    (result,) = of(events, "tool_result")
    assert result.payload == scene.row.result.output  # type: ignore[union-attr]
    assert scene.row.state == "done"
    assert scene.world.changed == [("put", '{"name":"tea","text":"mint"}')]


def test_a_saved_intent_whose_row_is_gone_is_a_bug_and_nothing_is_applied() -> None:
    scene = scene_of(asks(put("tea", "mint")))

    async def body(scene: Scene) -> None:
        log = scene.loop.log
        scene.loop.log = LosesTheResult(log)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.log = log
        # A store that lost the row, as no kill can make it.
        scene.loop.ledger = InMemoryEffectLedger(scene.loop.clock, 200_000)
        with pytest.raises(ValueError, match="has no row in the ledger"):
            await scene.turn()

    played(scene, body)
    assert len(scene.world.started) == 1


def test_before_and_after_tool_see_a_change_and_not_a_call_its_plan_answered() -> None:
    seen: Seen = []
    scene = scene_of(asks(put("tea", "mint")), asks(put("tea", "mint", call_id="c2")))
    scene.loop.observers = recording(seen, "a")
    played(scene)
    tools = [name for name, _ in seen if name.endswith("_tool")]
    assert tools == ["a.before_tool", "a.after_tool"]


def test_a_stop_known_before_apply_starts_it_never_and_fails_the_row() -> None:
    scene = scene_of(asks(put("tea", "mint")))

    async def stop() -> None:
        await stop_now(scene.loop, "s1")

    scene.tools.on_plan = stop
    seen: Seen = []
    scene.loop.observers = recording(seen, "a")
    events = played(scene)
    payload = result_of(events)
    assert payload["harness"] == "not_run"
    # Nothing ran, so nothing ended and no time was taken.
    assert [name for name, _ in seen if name.endswith("_tool")] == ["a.before_tool"]
    (result,) = of(events, "tool_result")
    assert result.duration_ms is None
    assert scene.row.state == "failed"
    assert scene.row.result == EffectResult("failed", payload)
    assert scene.world.started == []


def test_a_row_closed_before_its_call_runs_gives_its_saved_result() -> None:
    scene = scene_of(asks(put("tea", "mint")))
    closed = {"call_id": "c1", "text": "Closed.", "is_error": True}

    async def body(scene: Scene) -> None:
        killed = KillAfterBegin(scene.loop.ledger)
        scene.loop.ledger = killed  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.ledger = killed.inner  # type: ignore[assignment]
        finished = await scene.loop.ledger.finish(
            scene.row.effect_id, EffectResult("failed", closed)
        )
        assert finished is None
        assert isinstance(await scene.turn(), TurnEnd)

    events = played(scene, body)
    assert of(events, "tool_intent") == []
    assert result_of(events) == closed
    assert scene.world.started == []


def test_no_tool_observer_sees_a_change_that_is_settled() -> None:
    seen: Seen = []
    scene = scene_of(asks(put("tea", "mint")))

    async def body(scene: Scene) -> None:
        log = scene.loop.log
        scene.loop.log = LosesTheResult(log)  # type: ignore[assignment]
        assert isinstance(await scene.turn(), Failure)
        scene.loop.log = log
        scene.loop.observers = recording(seen, "a")
        assert isinstance(await scene.turn(), TurnEnd)

    played(scene, body)
    assert [name for name, _ in seen] == ["a.before_model", "a.after_model"]
