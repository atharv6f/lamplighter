"""The loop against the real Anthropic API. They cost money and need a key.

The default test run leaves these out. Run them by hand:

    uv run --env-file .env pytest -m live -s tests/live/test_loop.py

Each runs the real loop on a real SQLite store with the built-in tools on
a work folder of its own, and prints what each model call cost and what
the model said. Commands run behind the real wall, so these need macOS.
The page web_fetch reads is served by the test itself, behind a made-up
public address, so no request leaves this machine. Every prompt and page
here is made up.
"""

import asyncio
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from lamplighter.adapters import (
    FileBlobStore,
    ScriptedChannel,
    SystemClock,
    SystemIds,
)
from lamplighter.adapters.builtin_tools import BuiltinTools, WebAccess
from lamplighter.adapters.seatbelt import SeatbeltExecutor
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    open_database,
)
from lamplighter.app.chat import Tooling, run_spec
from lamplighter.app.code_revision import REPO_ROOT, code_revision
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import build_models
from lamplighter.app.tool_folders import make_tool_folders
from lamplighter.core import Deps, Observers, RunningSessions, Wake, drive, take
from lamplighter.ports import (
    BlobStore,
    Event,
    Executor,
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
from tests.builtin_kit import NO_WEB, PAGE_URL, served_page
from tests.stub_tools import StubTools
from tests.tooling_kit import with_tool_folders

pytestmark = pytest.mark.live

SETTINGS = Path(__file__).parents[2] / "lamplighter.toml"
SESSION = "live-loop"
FILE_ASK = (
    "Write the file kettle.txt with the one line: The kettle is blue."
    " Then read it back with read_file. Then change the word blue to green"
    " with edit_file."
)
COMMAND_ASK = (
    "Run the command: echo made-up-$((6*7)) with the shell tool, and tell me"
    " exactly what it printed."
)
PAGE_ASK = f"Fetch {PAGE_URL} with web_fetch and tell me the secret word on that page."
NOTE_ASK = "Write the file note.txt with the one line: Water the plants."
TIME_ASK = "What is the time now in UTC? Use the tool."
MORE_ASK = "And say it in words, please."

type Walled = Callable[[Config], Executor]
type Wrap = Callable[[ToolProvider], ToolProvider]


class Killed(BaseException):
    """The process dies inside a tool call."""


@pytest.fixture
def config() -> Config:
    return load_config(SETTINGS)


@pytest.fixture
def client(config: Config) -> ModelClient:
    models = build_models(config, os.environ["ANTHROPIC_API_KEY"], SystemClock())
    return models.clients[config.default_model().name]


def behind_the_wall(config: Config) -> Executor:
    tools = config.tools
    return SeatbeltExecutor(
        tools.work_dir, tools.scratch_dir, config.store.dir, SystemClock()
    )


@pytest.fixture
def walled() -> Walled:
    return behind_the_wall


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


class DiesInFirstApply:
    """The built-in tools, except that the first change dies before it starts.

    The intent and the ledger row are saved by then, as a kill would leave
    them, so the next process must settle the row with the tool's check.
    """

    def __init__(self, inner: ToolProvider) -> None:
        self._inner = inner
        self.died = False

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        return await self._inner.call(name, arguments)

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        return await self._inner.plan(name, arguments)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        if not self.died:
            self.died = True
            raise Killed
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
    walled: Walled,
    wrap: Wrap = lambda tools: tools,
    web: WebAccess = NO_WEB,
) -> Chat:
    """Save `first`, then drive the session until there is nothing left to do.

    The store is in `folder`, with the work and scratch folders beside it.
    The tools are the built-in ones behind the wall `walled` makes, given
    through `wrap`. A tool that dies ends this process, and a new one is
    started on the same store, as a restart would.
    """
    config = with_tool_folders(config, folder / "store")
    store = Path(config.store.dir)
    store.mkdir()
    assert make_tool_folders(config.tools) is None
    connection = open_database(store / "state.db", 5.0)
    assert not isinstance(connection, Failure), connection
    clock = SystemClock()
    limit = config.store.max_record_bytes
    channel = ScriptedChannel([], clock)
    blobs: BlobStore = FileBlobStore(
        store / "blobs", clock, config.store.max_blob_bytes
    )
    executor = walled(config)
    built = BuiltinTools(config.tools, blobs, executor, clock, web)
    tools = wrap(built)

    async def one_process(given: ToolProvider) -> None:
        deps = Deps(
            log=SqliteEventLog(connection, {}, limit),
            inbox=SqliteInbox(connection, limit),
            outbox=SqliteOutbox(connection, clock, limit),
            models={config.default_model().name: client},
            health={},
            tools=given,
            ledger=SqliteEffectLedger(connection, clock, limit),
            blobs=blobs,
            clock=clock,
            ids=SystemIds(),
            observers=Observers(),
            running=RunningSessions(),
        )
        wake = Wake()
        wake.end_input()
        spec = run_spec(
            config, Tooling(given, executor), code_revision(REPO_ROOT), False
        )
        assert await drive(SESSION, spec, deps, channel, wake, lambda: None) is None

    async def run_all() -> tuple[Event, ...]:
        saved = await SqliteInbox(connection, limit).accept(first)
        assert not isinstance(saved, Failure), saved
        # Each kill ends a process, and a new one carries on from the stores.
        for _ in range(3):
            try:
                await one_process(tools)
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


def intents(chat_: Chat) -> list[str]:
    return [str(e.payload["name"]) for e in of_type(chat_, "tool_intent")]


def test_the_model_writes_reads_and_edits_a_file(
    tmp_path: Path, config: Config, client: ModelClient, walled: Walled
) -> None:
    asked = typed("t1", FILE_ASK)
    done = chat(tmp_path, config, client, asked, walled)
    ended_done_with_a_reply(done)
    assert {"write_file", "read_file", "edit_file"} <= set(intents(done))
    results = of_type(done, "tool_result")
    assert not any(r.payload.get("harness") for r in results)
    work = Path(with_tool_folders(config, tmp_path / "store").tools.work_dir)
    assert (work / "kettle.txt").read_text().strip() == "The kettle is green."


def test_the_model_runs_a_command_behind_the_wall_and_says_what_it_printed(
    tmp_path: Path, config: Config, client: ModelClient, walled: Walled
) -> None:
    asked = typed("t1", COMMAND_ASK)
    done = chat(tmp_path, config, client, asked, walled)
    ended_done_with_a_reply(done)
    assert "shell" in intents(done)
    results = [str(r.payload["text"]) for r in of_type(done, "tool_result")]
    assert any("made-up-42" in text for text in results), results
    assert "made-up-42" in done.shown[0]


def test_the_model_answers_from_a_fetched_page_given_as_outside_text(
    tmp_path: Path, config: Config, client: ModelClient, walled: Walled
) -> None:
    asked = typed("t1", PAGE_ASK)
    done = chat(tmp_path, config, client, asked, walled, web=served_page())
    ended_done_with_a_reply(done)
    assert "web_fetch" in intents(done)
    fetched = [r for r in of_type(done, "tool_result") if r.payload.get("tainted")]
    assert fetched and "outside_text" in str(fetched[0].payload["text"])
    assert "periwinkle" in done.shown[0].lower()


def test_a_write_cut_off_after_its_intent_is_settled_and_written_again(
    tmp_path: Path, config: Config, client: ModelClient, walled: Walled
) -> None:
    asked = typed("t1", NOTE_ASK)
    done = chat(tmp_path, config, client, asked, walled, wrap=DiesInFirstApply)
    ended_done_with_a_reply(done)
    results = of_type(done, "tool_result")
    assert results[0].payload.get("harness") == "interrupted", results[0].payload
    assert "did not take effect" in str(results[0].payload["text"])
    assert intents(done).count("write_file") >= 2
    work = Path(with_tool_folders(config, tmp_path / "store").tools.work_dir)
    assert (work / "note.txt").read_text().strip() == "Water the plants."


def test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
    tmp_path: Path, config: Config, client: ModelClient, walled: Walled
) -> None:
    asked = typed("t1", TIME_ASK)
    more = typed("t2", MORE_ASK)
    opened: list[sqlite3.Connection] = []

    def stubs_saying_more(built: ToolProvider) -> ToolProvider:
        # The clock tool of the stubs, on the inbox of the chat's own store.
        store = with_tool_folders(config, tmp_path / "store").store.dir
        connection = open_database(Path(store) / "state.db", 5.0)
        assert not isinstance(connection, Failure)
        opened.append(connection)
        inbox = SqliteInbox(connection, config.store.max_record_bytes)
        return SaysMoreDuringFirstCall(StubTools(SystemClock()), inbox, more)

    try:
        done = chat(tmp_path, config, client, asked, walled, wrap=stubs_saying_more)
    finally:
        for connection in opened:
            connection.close()
    ended_done_with_a_reply(done)
    said = [e.payload["text"] for e in of_type(done, "user_message")]
    assert said == [asked.text, more.text]
    # The second message was saved after the first result, in the same run.
    first_result = of_type(done, "tool_result")[0].seq
    assert of_type(done, "user_message")[1].seq > first_result
    assert len(of_type(done, "run_started")) == 1
