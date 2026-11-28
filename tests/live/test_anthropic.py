"""Calls to the real Anthropic API. They cost money and need a key.

The default test run leaves these out. Run them by hand:

    uv run --env-file .env pytest -m live -s

The file `.env` holds one line, ANTHROPIC_API_KEY=..., and git ignores it.
Every prompt here is made up.
"""

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import cast

import pytest

from lamplighter.adapters import SystemClock
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import build_models
from lamplighter.core import (
    MODEL_RESPONSE,
    TOOL_RESULT,
    Reply,
    cost_micro_dollars,
    fold_reply,
    rebuild_messages,
)
from lamplighter.ports import (
    USER_MESSAGE,
    CacheHint,
    ErrorEvent,
    Failure,
    JsonObject,
    Message,
    ModelClient,
    ModelRequest,
    NewEvent,
    ReasoningPart,
    StopEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    as_saved,
    inbound_event_id,
    to_plain,
)

pytestmark = pytest.mark.live

SETTINGS = Path(__file__).parents[2] / "lamplighter.toml"

# Long enough to be cached: the API caches nothing below 512 tokens on this
# model, and gives no error when it does not.
HOUSE_RULES = "You keep a household's lists. Follow every rule below.\n" + "\n".join(
    f"Rule {n}: when a list has {n} items, say so before reading item {n}."
    for n in range(1, 301)
)

# The model decides for itself whether to reason, and skips it on an easy
# prompt. This one takes a few steps to work out which file to read.
PUZZLE = (
    "Three files hold my lists: a.txt, b.txt and c.txt. One holds the"
    " shopping, one the chores and one the phone numbers. The shopping is"
    " not in the file whose name comes first. The file c.txt holds the"
    " chores or the shopping. The chores are in the file right after the"
    " one with the phone numbers. Work out which file holds the shopping,"
    " read it with the tool, and tell me what is on it."
)

AT = "2026-11-26T07:35:12.345Z"
SESSION = "live-a"

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file from the household's folder and give its lines.",
    arguments_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The file name."}},
        "required": ["path"],
    },
)


@pytest.fixture
def config() -> Config:
    return load_config(SETTINGS)


@pytest.fixture
def client(config: Config) -> ModelClient:
    models = build_models(config, os.environ["ANTHROPIC_API_KEY"], SystemClock())
    return models.clients[config.default_model().name]


def user(*parts: TextPart | ToolResultPart) -> Message:
    return Message(role="user", parts=parts)


def request(
    *messages: Message, system: str = "Answer in one short line."
) -> ModelRequest:
    return ModelRequest(
        system=(system,),
        messages=messages,
        tools=(),
        max_output=2_000,
        cache_hints=(),
        deadline_ms=120_000,
        retries=2,
    )


async def ask(client: ModelClient, asked: ModelRequest) -> Reply:
    reply = fold_reply([event async for event in client.stream(asked)])
    if isinstance(reply.end, ErrorEvent):
        failure = reply.end.failure
        pytest.fail(f"{failure.kind}: {failure.message} {dict(failure.detail)}")
    return reply


def prompt_size(usage: Usage) -> int:
    counts = (usage.input_uncached, usage.cache_read, usage.cache_write)
    assert None not in counts, f"the server left a count out: {usage}"
    return sum(count for count in counts if count is not None)


def show(label: str, reply: Reply, config: Config) -> None:
    usage = reply.usage
    assert usage is not None, "the server reported no usage"
    prices = config.default_model().prices
    assert prices is not None
    cost = cost_micro_dollars(usage, prices)
    dollars = "unknown" if cost is None else f"${cost / 1_000_000:.6f}"
    print(
        f"\n{label}: uncached {usage.input_uncached}, cache read {usage.cache_read},"
        f" cache write {usage.cache_write}, output {usage.output}, cost {dollars},"
        f" unknown events skipped {reply.end.skipped}"
    )


def test_a_second_call_with_the_same_long_prompt_reads_from_the_cache(
    client: ModelClient, config: Config
) -> None:
    asked = dataclasses.replace(
        request(user(TextPart(text="How many rules are there?")), system=HOUSE_RULES),
        cache_hints=(CacheHint(after="system", index=0),),
    )

    async def two_calls() -> tuple[Reply, Reply]:
        return await ask(client, asked), await ask(client, asked)

    first, second = asyncio.run(two_calls())
    show("first call", first, config)
    show("second call", second, config)
    assert second.usage is not None
    assert second.usage.cache_read is not None
    assert second.usage.cache_read > 0


async def round_trip(client: ModelClient, question: Message) -> tuple[Reply, Reply]:
    """Ask, run the tool the model calls, and send the result back."""
    first_ask = dataclasses.replace(request(question), tools=(READ_FILE,))
    first = await ask(client, first_ask)
    calls = [p for p in first.message.parts if isinstance(p, ToolCallPart)]
    assert calls, f"the model called no tool: {first.message.parts}"
    results = [
        ToolResultPart(call_id=call.id, text="milk\neggs\ntea", is_error=False)
        for call in calls
    ]
    # The reply goes back exactly as it came, reasoning included.
    second_ask = dataclasses.replace(
        first_ask, messages=(question, first.message, user(*results))
    )
    return first, await ask(client, second_ask)


def reasoning_in(reply: Reply) -> list[ReasoningPart]:
    return [p for p in reply.message.parts if isinstance(p, ReasoningPart)]


def test_a_tool_call_and_its_result_make_a_round_trip(
    client: ModelClient, config: Config
) -> None:
    question = user(
        TextPart(text="Read list.txt with the tool, then tell me what is on it.")
    )
    first, second = asyncio.run(round_trip(client, question))
    show("call that asks for the tool", first, config)
    show("call that carries the result", second, config)
    print(f"reasoning parts carried back: {len(reasoning_in(first))}")
    assert isinstance(first.end, StopEvent)
    assert first.end.reason == "tool_calls"
    assert isinstance(second.end, StopEvent)
    assert second.end.reason == "end"
    assert any(isinstance(p, TextPart) and p.text for p in second.message.parts)


def test_reasoning_made_before_a_tool_call_goes_back_and_is_accepted(
    client: ModelClient, config: Config
) -> None:
    first, second = asyncio.run(round_trip(client, user(TextPart(text=PUZZLE))))
    show("call that reasons, then asks for the tool", first, config)
    show("call that carries the reasoning back", second, config)
    print(f"reasoning parts carried back: {len(reasoning_in(first))}")
    assert reasoning_in(first), "the model did not reason, so nothing was sent back"
    # A reasoning part the server did not accept would have failed the call.
    assert isinstance(second.end, StopEvent)
    assert second.end.reason == "end"


def saved_event(kind: str, record: object) -> NewEvent:
    return NewEvent(type=kind, v=1, payload=cast(JsonObject, to_plain(record)), at=AT)


async def save_and_rebuild(
    folder: Path, config: Config, expected_seq: int, *events: NewEvent
) -> tuple[Message, ...]:
    """Save events, then rebuild the transcript on a new connection, as on restart."""
    for_writing = open_database(folder / "state.db", 5)
    assert not isinstance(for_writing, Failure), for_writing
    limit = config.store.max_record_bytes
    try:
        saved = await SqliteEventLog(for_writing, {}, limit).append(
            SESSION, events, expected_seq
        )
        assert not isinstance(saved, Failure), saved
    finally:
        for_writing.close()
    for_reading = open_database(folder / "state.db", 5)
    assert not isinstance(for_reading, Failure), for_reading
    try:
        read = await SqliteEventLog(for_reading, {}, limit).read(SESSION, 0)
    finally:
        for_reading.close()
    assert not isinstance(read, Failure), read
    rebuilt = rebuild_messages(read)
    assert not isinstance(rebuilt, Failure), rebuilt
    return rebuilt


async def saved_round_trip(
    client: ModelClient, config: Config, folder: Path
) -> tuple[Reply, Reply]:
    """Do the round trip with every message sent from its saved form."""
    question = dataclasses.replace(
        saved_event(USER_MESSAGE, TextPart(text=PUZZLE)),
        event_id=inbound_event_id("terminal", "live-question-1"),
    )
    asked = await save_and_rebuild(folder, config, 0, question)
    first_ask = dataclasses.replace(request(*asked), tools=(READ_FILE,))
    first = await ask(client, first_ask)
    calls = [p for p in first.message.parts if isinstance(p, ToolCallPart)]
    assert calls, f"the model called no tool: {first.message.parts}"
    results = [
        saved_event(
            TOOL_RESULT,
            ToolResultPart(call_id=call.id, text="milk\neggs\ntea", is_error=False),
        )
        for call in calls
    ]
    reply = saved_event(MODEL_RESPONSE, first)
    messages = await save_and_rebuild(folder, config, 1, reply, *results)
    assert messages[1] == as_saved(Message, first.message)
    return first, await ask(client, dataclasses.replace(first_ask, messages=messages))


def test_a_reply_saved_in_the_store_and_rebuilt_is_accepted(
    client: ModelClient, config: Config, tmp_path: Path
) -> None:
    # After a restart a reply goes back to the model from its saved form, so
    # the API must accept that form, reasoning data included.
    first, second = asyncio.run(saved_round_trip(client, config, tmp_path))
    show("call that reasons, then asks for the tool", first, config)
    show("call sent from the saved store", second, config)
    print(f"reasoning parts saved and carried back: {len(reasoning_in(first))}")
    assert reasoning_in(first), "the model did not reason, so nothing was sent back"
    assert isinstance(second.end, StopEvent)
    assert second.end.reason == "end"


def test_a_token_count_is_as_close_to_the_real_size_as_the_profile_says(
    client: ModelClient, config: Config
) -> None:
    short = request(user(TextPart(text="Is the kettle on?")))
    asks = {
        "a short prompt": short,
        "a prompt with a tool": dataclasses.replace(short, tools=(READ_FILE,)),
        "a long prompt": dataclasses.replace(short, system=(HOUSE_RULES,)),
    }

    async def count_and_call(asked: ModelRequest) -> tuple[int, int]:
        counted = await client.count_tokens(asked)
        assert isinstance(counted, int), f"the count failed: {counted}"
        reply = await ask(client, dataclasses.replace(asked, max_output=64))
        assert reply.usage is not None
        return counted, prompt_size(reply.usage)

    async def all_of_them() -> dict[str, tuple[int, int]]:
        return {label: await count_and_call(asked) for label, asked in asks.items()}

    worst = 0
    for label, (counted, real) in asyncio.run(all_of_them()).items():
        gap = -(-abs(counted - real) * 1000 // real)
        worst = max(worst, gap)
        print(f"\n{label}: counted {counted}, real {real}, gap {gap} per thousand")
    print(f"\nlargest gap: {worst} per thousand")
    assert worst <= config.default_model().profile.count_error_permille
