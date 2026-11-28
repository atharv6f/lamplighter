"""A private chat: only models on this Mac, web access closed, and for good.

The settings have a model on this Mac with a private route to it, and a
frontier model on the default route. Each model is a made-up one that
counts its calls. The open tools and the private tools are told apart by
their names, which the model sees in each request.
"""

import asyncio
import dataclasses
import io
import threading
from pathlib import Path

from lamplighter.adapters import ScriptedReply, SystemClock
from lamplighter.adapters.in_memory import (
    InMemoryBlobStore,
    InMemoryTraceCursor,
    InMemoryTraceSink,
)
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.app.chat import PRIVATE_LINE, ChatParts, chat, run_spec
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import chat_parts
from lamplighter.app.model_set import ModelSet
from lamplighter.core import UPCASTERS
from lamplighter.core.harness_texts import private_no_model_notice
from lamplighter.ports import Event, Failure, ModelRequest, NewEvent, TextPart, Usage
from tests.ladder_kit import DOWN, SteeredHealth
from tests.loop_kit import note_tools
from tests.model_by_request import ModelByRequest
from tests.run_history import AT
from tests.settings_text import with_local
from tests.stub_tools import StubTools
from tests.terminal_kit import Keyboard, Screen, typed
from tests.test_chat_command import REVISION, Saved, fill
from tests.tooling_kit import tooling_for, with_tool_folders

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
CHAT_SECONDS = 20.0
SESSION = "kitchen"


def answered(said: str) -> ModelByRequest:
    def choose(request: ModelRequest) -> ScriptedReply:
        del request
        return ScriptedReply((TextPart(text=said),), "end", USAGE)

    return ModelByRequest(choose)


class Chat:
    """The settings, the two models and the two sets of tools of one test."""

    def __init__(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.toml"
        settings.write_text(with_local())
        config = with_tool_folders(load_config(settings), tmp_path / "store")
        self.config: Config = dataclasses.replace(
            config, run=dataclasses.replace(config.run, model_max_output=1_000)
        )
        self.local = answered("From this Mac.")
        self.frontier = answered("From the frontier.")
        self.health = SteeredHealth()
        clock = SystemClock()
        self.open_tools = tooling_for(self.config, StubTools(clock))
        self.private_tools = tooling_for(self.config, note_tools(clock))

    def parts(
        self, keyboard: Keyboard, screen: Screen, errors: io.StringIO
    ) -> ChatParts:
        async def close() -> None:
            pass

        models = ModelSet(
            {"local": self.local, "frontier": self.frontier},
            {"local": self.health},
            close,
        )
        return chat_parts(
            self.config,
            models,
            self.open_tools,
            self.private_tools,
            InMemoryTraceSink(),
            InMemoryTraceCursor(),
            keyboard.reader,
            screen,
            errors,
            REVISION,
        )

    def run(self, session: str | None, private: bool) -> tuple[int, str, str]:
        screen, errors = Screen(), io.StringIO()
        keyboard = typed(b"Hello.\n")
        parts = self.parts(keyboard, screen, errors)
        timed_out = threading.Event()
        watchdog = threading.Timer(CHAT_SECONDS, timed_out.set)
        watchdog.start()
        try:
            code = chat(self.config, session, parts, private)
        finally:
            watchdog.cancel()
            keyboard.done()
        assert not timed_out.is_set(), "the chat did not end by itself"
        return code, screen.getvalue(), errors.getvalue()

    def events(self, session: str = SESSION) -> tuple[Event, ...]:
        connection = open_database(Path(self.config.store.dir) / "state.db", 5.0)
        assert not isinstance(connection, Failure), connection
        try:
            log = SqliteEventLog(connection, UPCASTERS, 100_000)
            events = asyncio.run(log.read(session, 0))
        finally:
            connection.close()
        assert not isinstance(events, Failure), events
        return events

    def ordinary_id(self) -> str:
        """Give the config id of these settings and tools when not private.

        The private tools are used, so the flag is the only thing that
        differs from the private chat's id, as with the real tools, whose
        list is the same with the web open or closed.
        """
        tooling = self.private_tools(InMemoryBlobStore(10))
        return run_spec(self.config, tooling, REVISION, False).config_id


def started(events: tuple[Event, ...]) -> list[object]:
    return [e.payload["private"] for e in events if e.type == "run_started"]


def tool_names(model: ModelByRequest) -> set[str]:
    return {spec.name for request in model.requests for spec in request.tools}


def test_a_private_chat_says_so_and_uses_only_this_mac(tmp_path: Path) -> None:
    made = Chat(tmp_path)
    code, screen, errors = made.run(SESSION, private=True)
    assert (code, errors) == (0, "")
    assert PRIVATE_LINE in screen.splitlines()
    assert "From this Mac." in screen
    assert (len(made.local.requests), len(made.frontier.requests)) == (1, 0)
    assert tool_names(made.local) == {"note", "tick"}
    assert started(made.events()) == [True]
    assert PRIVATE_LINE == (
        "This session is private: only models on this Mac are used, and web"
        " access is off."
    )


def test_a_private_session_stays_private_without_the_flag(tmp_path: Path) -> None:
    made = Chat(tmp_path)
    made.run(SESSION, private=True)
    code, screen, _ = made.run(SESSION, private=False)
    assert code == 0
    assert PRIVATE_LINE in screen.splitlines()
    assert (len(made.local.requests), len(made.frontier.requests)) == (2, 0)
    assert tool_names(made.local) == {"note", "tick"}
    assert started(made.events()) == [True, True]


def test_an_ordinary_chat_says_nothing_and_has_the_open_tools(tmp_path: Path) -> None:
    made = Chat(tmp_path)
    code, screen, _ = made.run(SESSION, private=False)
    assert code == 0
    assert PRIVATE_LINE not in screen
    assert (len(made.local.requests), len(made.frontier.requests)) == (0, 1)
    assert tool_names(made.frontier) == {"current_time", "wait"}
    assert started(made.events()) == [False]


def test_a_new_private_session_has_its_id_and_the_line(tmp_path: Path) -> None:
    made = Chat(tmp_path)
    code, screen, _ = made.run(None, private=True)
    assert code == 0
    lines = screen.splitlines()
    session = lines[0].removeprefix("Session ")
    assert lines[1] == PRIVATE_LINE
    assert started(made.events(session)) == [True]


def test_going_private_ends_an_open_ordinary_run_as_changed_settings(
    tmp_path: Path,
) -> None:
    made = Chat(tmp_path)
    ordinary = made.ordinary_id()

    async def open_run(saved: Saved) -> None:
        payload = {"config_id": ordinary, "trigger": "message", "private": False}
        run = NewEvent(type="run_started", v=2, payload=payload, at=AT, run_id="r-1")
        assert await saved.log.append(SESSION, [run], 0) == 1

    fill(tmp_path, open_run)
    code, _, _ = made.run(SESSION, private=True)
    assert code == 0
    events = made.events()
    ends = [e.payload for e in events if e.type == "run_end"]
    assert ends == [
        {"reason": "failed", "detail": "config_changed"},
        {"reason": "done", "detail": ""},
    ]
    assert started(events) == [False, True]
    assert (len(made.local.requests), len(made.frontier.requests)) == (1, 0)


def test_a_private_chat_with_its_server_down_sends_nothing_off_this_mac(
    tmp_path: Path,
) -> None:
    made = Chat(tmp_path)
    made.health.answer = DOWN
    code, screen, _ = made.run(SESSION, private=True)
    assert code == 0
    assert private_no_model_notice(("local",)) in screen
    assert (made.local.requests, made.frontier.requests) == ([], [])
    assert "model_request" not in {e.type for e in made.events()}
