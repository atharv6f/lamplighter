"""A run moves up its ladder on each signal, and only when the rules allow it."""

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, Mapping

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.core import ROUTE_DECIDED, Route, TurnEnd, run_turn
from lamplighter.core.harness_texts import (
    no_model_notice,
    private_no_model_notice,
    with_repeat_warning,
)
from lamplighter.core.run_spec import RunSpec
from lamplighter.ports import (
    Event,
    EventLog,
    Failure,
    FailureKind,
    HealthCheck,
    Inbox,
    ModelClient,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    TextPart,
    ToolCallPart,
    Usage,
)
from tests.ladder_kit import DOWN, FRONTIER, LADDER, LOCAL, SteeredHealth, private
from tests.loop_kit import in_memory, in_order
from tests.model_by_request import Choose, ModelByRequest
from tests.run_history import SESSION, inbound, stop
from tests.test_run_turn import FailingLog

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
ANSWER = "Made-up answer from the model above."
EMPTY = ScriptedReply((), "end", USAGE)
REFUSAL = ScriptedReply((), "refused", USAGE)
DONE = ScriptedReply((TextPart(text=ANSWER),), "end", USAGE)


def failed(kind: FailureKind, *parts: TextPart) -> ScriptedBreak:
    failure = Failure(kind=kind, effect="none", message="Made-up failure.", source="t")
    return ScriptedBreak(parts, failure)


def calls(*parts: ToolCallPart) -> ScriptedReply:
    return ScriptedReply(parts, "tool_calls", USAGE)


def wait(call_id: str) -> ToolCallPart:
    return ToolCallPart(id=call_id, name="wait", arguments={"seconds": 1})


def unknown(call_id: str) -> ToolCallPart:
    return ToolCallPart(id=call_id, name="weather", arguments={"city": "Lisbon"})


class StopSavedWhileAsked:
    """A model that saves a stop while it is asked. Satisfies ModelClient.

    The stop is saved and its flag not raised, so the call is not cut short
    and its reply is saved with the stop waiting behind it.
    """

    def __init__(self, inner: ModelByRequest, inbox: Inbox) -> None:
        self.inner = inner
        self.inbox = inbox

    @property
    def profile(self) -> ModelProfile:
        return self.inner.profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        accepted = await self.inbox.accept(stop("s1"))
        assert not isinstance(accepted, Failure), accepted
        async for event in self.inner.stream(request):
            yield event

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        return await self.inner.count_tokens(request)


class Ladder:
    """The loop on fakes, with a scripted model at each place of the ladder."""

    def __init__(
        self,
        local: Choose,
        frontier: Choose,
        health: Mapping[str, HealthCheck] | None = None,
    ) -> None:
        self.local = ModelByRequest(local)
        self.frontier = ModelByRequest(frontier)
        self.clients: dict[str, ModelClient] = {
            LOCAL: self.local,
            FRONTIER: self.frontier,
        }
        self.loop = in_memory(self.local)
        self.health = dict(health or {})

    async def run(self, spec: RunSpec = LADDER) -> TurnEnd | Failure | None:
        deps = dataclasses.replace(
            self.loop.deps,
            models=self.clients,
            health=self.health,
        )
        return await run_turn(SESSION, spec, deps)

    async def ask(self, spec: RunSpec = LADDER) -> TurnEnd | Failure | None:
        accepted = await self.loop.inbox.accept(inbound("n1", "Hello."))
        assert not isinstance(accepted, Failure), accepted
        return await self.run(spec)

    async def events(self) -> tuple[Event, ...]:
        return await self.loop.events(SESSION)

    async def reasons(self) -> list[object]:
        return [
            e.payload["reason"] for e in await self.events() if e.type == ROUTE_DECIDED
        ]

    async def end(self) -> tuple[object, object]:
        last = (await self.events())[-1]
        assert last.type == "run_end"
        return (last.payload["reason"], last.payload["detail"])

    async def said(self) -> list[str]:
        return [row.text for row in await self.loop.due()]


def test_two_empty_replies_move_the_run_up_and_the_new_model_gets_its_retry() -> None:
    async def scenario() -> None:
        half = failed("transient", TextPart(text="Half an ans"))
        ladder = Ladder(in_order(EMPTY, EMPTY), in_order(half, DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "empty_reply"]
        # The failed reply that gave some content is tried once more: the
        # count of bad replies started again at the choice.
        assert (len(ladder.local.requests), len(ladder.frontier.requests)) == (2, 2)
        assert await ladder.said() == [ANSWER]

    asyncio.run(scenario())


def test_a_refusal_moves_the_run_up() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(REFUSAL), in_order(DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "refused"]
        assert await ladder.said() == [ANSWER]

    asyncio.run(scenario())


def test_a_failed_call_moves_the_run_up() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "model_failed"]
        assert len(ladder.frontier.requests) == 1
        events = await ladder.events()
        assert [e.type for e in events] == [
            "session_started",
            "run_started",
            "user_message",
            "route_decided",
            "model_request",
            "model_response",
            "route_decided",
            "model_request",
            "model_response",
            "reply_ready",
            "run_end",
        ]
        asked = [e.payload["model_name"] for e in events if e.type == "model_request"]
        assert asked == [LOCAL, FRONTIER]

    asyncio.run(scenario())


def test_a_prompt_too_large_moves_the_run_to_a_larger_window() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("too_large")), in_order(DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "too_large"]

    asyncio.run(scenario())


def test_two_wrong_calls_in_a_row_move_the_run_up_at_its_next_call() -> None:
    async def scenario() -> None:
        local = in_order(calls(unknown("call_1")), calls(unknown("call_2")))
        ladder = Ladder(local, in_order(DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "invalid_calls"]
        assert (len(ladder.local.requests), len(ladder.frontier.requests)) == (2, 1)

    asyncio.run(scenario())


def test_a_repeated_call_moves_the_run_up_and_the_new_model_may_make_it_once() -> None:
    async def scenario() -> None:
        local = in_order(*(calls(wait(f"call_{n}")) for n in range(1, 4)))
        ladder = Ladder(local, in_order(calls(wait("call_4")), DONE))
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start", "repeated_call"]
        texts = [
            e.payload["text"] for e in await ladder.events() if e.type == "tool_result"
        ]
        warned = with_repeat_warning("Waited 1 seconds.")
        # The third same call is warned. The new model's first is not counted
        # against the old model's three, so it is neither warned nor ended.
        assert texts == [
            "Waited 1 seconds.",
            "Waited 1 seconds.",
            warned,
            "Waited 1 seconds.",
        ]

    asyncio.run(scenario())


def test_a_run_at_the_top_of_its_ladder_ends_as_it_did_before() -> None:
    async def scenario() -> None:
        ladder = Ladder(
            in_order(failed("invalid_input")), in_order(failed("invalid_input"))
        )
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "model_invalid_input")
        assert await ladder.reasons() == ["start", "model_failed"]

    asyncio.run(scenario())


def test_with_a_stop_waiting_a_failed_reply_ends_the_run_as_it_did_before() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        accepted = await ladder.loop.inbox.accept(inbound("n1", "Hello."))
        assert not isinstance(accepted, Failure)

        ladder.clients[LOCAL] = StopSavedWhileAsked(ladder.local, ladder.loop.inbox)
        ended = await ladder.run()
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "model_invalid_input")
        assert ladder.frontier.requests == []
        assert await ladder.reasons() == ["start"]
        assert await ladder.loop.pending() == ()

    asyncio.run(scenario())


def test_with_a_limit_reached_a_failed_reply_ends_the_run_as_it_did_before() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        ended = await ladder.ask(dataclasses.replace(LADDER, max_steps=1))
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "model_invalid_input")
        assert ladder.frontier.requests == []
        assert await ladder.reasons() == ["start"]

    asyncio.run(scenario())


def test_a_run_under_other_settings_does_not_move_up() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        inner: EventLog = ladder.loop.log
        # The fifth append is the second choice, lost as a kill would lose it.
        ladder.loop.log = FailingLog(inner, failing=5)
        assert isinstance(await ladder.ask(), Failure)
        ladder.loop.log = inner
        ended = await ladder.run(dataclasses.replace(LADDER, config_id="d" * 64))
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "model_invalid_input")
        assert ladder.frontier.requests == []
        assert await ladder.reasons() == ["start"]

    asyncio.run(scenario())


def test_a_kill_after_the_choice_carries_on_with_the_new_model() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        inner: EventLog = ladder.loop.log
        # The sixth append is the request to the new model.
        ladder.loop.log = FailingLog(inner, failing=6)
        assert isinstance(await ladder.ask(), Failure)
        ladder.loop.log = inner
        ended = await ladder.run()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        # The failed reply is older than the choice, so it leads to nothing.
        assert await ladder.reasons() == ["start", "model_failed"]
        assert (len(ladder.local.requests), len(ladder.frontier.requests)) == (1, 1)
        assert await ladder.said() == [ANSWER]

    asyncio.run(scenario())


def test_a_private_run_whose_local_model_is_down_calls_nothing() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(DONE), in_order(DONE), {LOCAL: SteeredHealth(DOWN)})
        ended = await ladder.ask(private())
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "no_model")
        assert await ladder.reasons() == []
        assert (ladder.local.requests, ladder.frontier.requests) == ([], [])
        assert await ladder.said() == [private_no_model_notice((LOCAL,))]

    asyncio.run(scenario())


def test_a_private_run_never_moves_to_a_model_that_leaves_this_mac() -> None:
    async def scenario() -> None:
        ladder = Ladder(in_order(failed("invalid_input")), in_order(DONE))
        ended = await ladder.ask(private())
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "model_invalid_input")
        assert ladder.frontier.requests == []

    asyncio.run(scenario())


def test_a_standing_signal_with_nothing_above_up_goes_on_with_the_current_model() -> (
    None
):
    async def scenario() -> None:
        frontier = SteeredHealth(DOWN)
        local = in_order(calls(unknown("call_1")), calls(unknown("call_2")), DONE)
        ladder = Ladder(local, in_order(DONE), {FRONTIER: frontier})
        ended = await ladder.ask()
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert await ladder.reasons() == ["start"]
        assert ladder.frontier.requests == []
        # Asked at the start and again once the signal stood, and nothing
        # was saved for the answer that kept the current model.
        assert frontier.asked == 2

    asyncio.run(scenario())


def test_an_ordinary_run_with_no_model_up_says_so_and_calls_nothing() -> None:
    async def scenario() -> None:
        down = {LOCAL: SteeredHealth(DOWN)}
        spec = dataclasses.replace(
            LADDER, routes=(Route("default", "always", (LOCAL,)),)
        )
        ladder = Ladder(in_order(DONE), in_order(DONE), down)
        ended = await ladder.ask(spec)
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        assert await ladder.end() == ("failed", "no_model")
        assert ladder.local.requests == []
        assert await ladder.said() == [no_model_notice((LOCAL,))]

    asyncio.run(scenario())


def test_a_later_run_of_a_private_session_stays_on_this_mac() -> None:
    async def scenario() -> None:
        local = SteeredHealth()
        ladder = Ladder(in_order(DONE, DONE), in_order(DONE), {LOCAL: local})
        first = await ladder.ask(private())
        assert isinstance(first, TurnEnd) and first.reason == "done"
        local.answer = DOWN
        accepted = await ladder.loop.inbox.accept(inbound("n2", "Again."))
        assert not isinstance(accepted, Failure)
        second = await ladder.run(LADDER)
        assert isinstance(second, TurnEnd) and second.reason == "failed"
        assert await ladder.end() == ("failed", "no_model")
        assert ladder.frontier.requests == []
        said = await ladder.said()
        assert said[-1] == private_no_model_notice((LOCAL,))

    asyncio.run(scenario())
