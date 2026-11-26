"""The loop against the real Anthropic API. They cost money and need a key.

The default test run leaves these out. Run them by hand:

    uv run --env-file .env pytest -m live -s tests/live/test_loop.py

Each runs the real loop on a real SQLite store with the stub tools, and
prints what each model call cost and what the model said. Every prompt
here is made up.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from lamplighter.adapters import (
    FileBlobStore,
    ScriptedChannel,
    StubTools,
    SystemClock,
    SystemIds,
)
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    open_database,
)
from lamplighter.app.chat import run_spec
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import build_model
from lamplighter.core import Deps, Observers, RunningSessions, Wake, drive, take
from lamplighter.ports import (
    Event,
    Failure,
    Inbound,
    JsonObject,
    ModelClient,
    Plan,
    Sender,
    ToolEntry,
    ToolProvider,
    ToolResult,
    utc_text,
)

pytestmark = pytest.mark.live

SETTINGS = Path(__file__).parents[2] / "lamplighter.toml"
SESSION = "live-loop"


class Killed(BaseException):
    """The process dies inside a tool call."""


@pytest.fixture
def config() -> Config:
    return load_config(SETTINGS)


@pytest.fixture
def client(config: Config) -> ModelClient:
    return build_model(config, os.environ["ANTHROPIC_API_KEY"])


def typed(native_id: str, text: str) -> Inbound:
    now = utc_text(SystemClock().now())
    return Inbound(
        channel="terminal",
        native_id=native_id,
        conversation=SESSION,
        sender=Sender(id="local", verified=True),
        kind="message",
        text=text,
        sent_at=now,
        received_at=now,
    )


@dataclass
class Chat:
    """One session on a real store: what was saved and what was shown."""

    events: tuple[Event, ...]
    shown: list[str]


class DiesInFirstTwoCalls:
    """The stub tools, except that the first two calls die part way, as kills would.

    A read cut off by a kill is run once more, so it takes two kills before
    the loop answers the call itself.
    """

    def __init__(self, inner: ToolProvider) -> None:
        self._inner = inner
        self.died = 0

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        if self.died < 2:
            self.died += 1
            raise Killed
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


class SaysMoreDuringFirstCall:
    """The stub tools, except that a second message arrives during the first call."""

    def __init__(self, inner: ToolProvider, inbox: SqliteInbox, more: Inbound) -> None:
        self._inner = inner
        self._inbox = inbox
        self._more = more
        self.said = False

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        if not self.said:
            self.said = True
            channel = ScriptedChannel([], SystemClock())
            running = RunningSessions()
            await take(self._more, self._inbox, channel, running, lambda: None)
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


def chat(
    folder: Path,
    config: Config,
    client: ModelClient,
    first: Inbound,
    tools: ToolProvider | None = None,
) -> Chat:
    """Save `first`, then drive the session until there is nothing left to do.

    A tool that dies ends this process. A new one is started on the same
    store with the plain stub tools, as a restart would.
    """
    connection = open_database(folder / "state.db", 5.0)
    assert not isinstance(connection, Failure), connection
    clock = SystemClock()
    limit = config.store.max_record_bytes
    channel = ScriptedChannel([], clock)
    stub = StubTools(clock)

    async def one_process(given: ToolProvider) -> None:
        deps = Deps(
            log=SqliteEventLog(connection, {}, limit),
            inbox=SqliteInbox(connection, limit),
            outbox=SqliteOutbox(connection, clock, limit),
            model=client,
            tools=given,
            ledger=SqliteEffectLedger(connection, clock, limit),
            blobs=FileBlobStore(folder / "blobs", clock, config.store.max_blob_bytes),
            clock=clock,
            ids=SystemIds(),
            observers=Observers(),
            running=RunningSessions(),
        )
        wake = Wake()
        wake.end_input()
        spec = run_spec(config, stub)
        assert await drive(SESSION, spec, deps, channel, wake, lambda: None) is None

    async def run_all() -> tuple[Event, ...]:
        saved = await SqliteInbox(connection, limit).accept(first)
        assert not isinstance(saved, Failure), saved
        # Each kill ends a process, and a new one carries on from the stores.
        for _ in range(3):
            try:
                await one_process(tools or stub)
                break
            except Killed:
                pass
        else:
            raise AssertionError("the chat was killed three times")
        events = await SqliteEventLog(connection, {}, limit).read(SESSION, 0)
        assert not isinstance(events, Failure), events
        return events

    try:
        events = asyncio.run(run_all())
    finally:
        connection.close()
    shown = [out.text for out in channel.sent]
    _show(events, shown)
    return Chat(events, shown)


def _show(events: tuple[Event, ...], shown: list[str]) -> None:
    for event in events:
        if event.type == "model_response":
            cost = event.payload.get("cost_micro_dollars")
            dollars = "unknown" if not isinstance(cost, int) else f"${cost / 1e6:.6f}"
            print(f"\nstep {event.step}: cost {dollars}, reply {_reply_text(event)!r}")
        if event.type == "tool_result":
            print(f"  result: {str(event.payload.get('text'))[:120]!r}")
    print(f"shown: {shown}")


def _reply_text(event: Event) -> str:
    message = event.payload.get("message")
    assert isinstance(message, dict)
    parts = message.get("parts")
    assert isinstance(parts, list)
    said: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("kind") == "text":
            said.append(str(part.get("text")))
        elif isinstance(part, dict) and part.get("kind") == "tool_call":
            said.append(f"<calls {part.get('name')}>")
    return " ".join(said)


def of_type(chat_: Chat, kind: str) -> list[Event]:
    return [event for event in chat_.events if event.type == kind]


def ended_done_with_a_reply(chat_: Chat) -> None:
    assert [e.payload["reason"] for e in of_type(chat_, "run_end")] == ["done"]
    assert len(chat_.shown) == 1 and chat_.shown[0].strip()
    for response in of_type(chat_, "model_response"):
        end = response.payload.get("end")
        assert isinstance(end, dict) and end.get("kind") == "stop", end


def test_a_whole_run_calls_the_clock_tool_and_answers(
    tmp_path: Path, config: Config, client: ModelClient
) -> None:
    asked = typed("t1", "What is the time now in UTC? Use the tool.")
    done = chat(tmp_path, config, client, asked)
    ended_done_with_a_reply(done)
    intents = [e.payload["name"] for e in of_type(done, "tool_intent")]
    assert "current_time" in intents
    results = of_type(done, "tool_result")
    assert results and not any(r.payload.get("harness") for r in results)


def test_a_call_cut_off_by_a_kill_is_answered_interrupted_and_the_api_accepts_it(
    tmp_path: Path, config: Config, client: ModelClient
) -> None:
    asked = typed("t1", "What is the time now in UTC? Use the tool.")
    done = chat(
        tmp_path, config, client, asked, DiesInFirstTwoCalls(StubTools(SystemClock()))
    )
    ended_done_with_a_reply(done)
    harness = [r.payload.get("harness") for r in of_type(done, "tool_result")]
    assert harness and harness[0] == "interrupted", harness
    # The request after the repair was made and answered.
    assert len(of_type(done, "model_response")) >= 2


def test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
    tmp_path: Path, config: Config, client: ModelClient
) -> None:
    asked = typed("t1", "What is the time now in UTC? Use the tool.")
    more = typed("t2", "And say it in words, please.")
    connection = open_database(tmp_path / "state.db", 5.0)
    assert not isinstance(connection, Failure)
    inbox = SqliteInbox(connection, config.store.max_record_bytes)
    tools = SaysMoreDuringFirstCall(StubTools(SystemClock()), inbox, more)
    done = chat(tmp_path, config, client, asked, tools)
    connection.close()
    ended_done_with_a_reply(done)
    said = [e.payload["text"] for e in of_type(done, "user_message")]
    assert said == [asked.text, more.text]
    # The second message was saved after the first result, in the same run.
    first_result = of_type(done, "tool_result")[0].seq
    assert of_type(done, "user_message")[1].seq > first_result
    assert len(of_type(done, "run_started")) == 1
