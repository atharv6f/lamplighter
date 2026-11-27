"""The chat command run in this process, on a real store, with a made-up model.

Input comes from a pipe and the screen is held in memory. A helper thread
plays the person: it waits for a line on the screen, then types or
presses Ctrl-C.
"""

import asyncio
import dataclasses
import io
import os
import signal
import stat
import sys
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import (
    ScriptedReply,
    SystemClock,
)
from lamplighter.adapters.in_memory import InMemoryBlobStore
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    open_database,
)
from lamplighter.app.chat import (
    LOCK_HELD,
    NO_WALL,
    UNSETTLED,
    WAITING,
    ChatParts,
    chat,
    run_spec,
)
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import NO_KEY, builtin_tooling, chat_parts, main
from lamplighter.app.single_instance import lock_store
from lamplighter.app.tool_folders import THROUGH_A_LINK
from lamplighter.core import config_id
from lamplighter.ports import (
    Command,
    EffectLedger,
    EventLog,
    ExecResult,
    Failure,
    Inbox,
    ModelRequest,
    NewEvent,
    Outbound,
    Outbox,
    TextPart,
    ToolCallPart,
    Usage,
    out_id_for,
)
from tests.model_by_request import Choose, ModelByRequest, assistant_turns
from tests.model_samples import PROFILE
from tests.run_history import AT, inbound
from tests.store_samples import action
from tests.stub_tools import StubTools
from tests.terminal_kit import Keyboard, Screen, typed
from tests.tooling_kit import ENDED_WELL, Answer, tooling_for, with_tool_folders

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"
CHAT_SECONDS = 20.0
USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)


def config_in(tmp_path: Path) -> Config:
    config = with_tool_folders(load_config(SETTINGS), tmp_path / "store")
    return dataclasses.replace(
        config,
        model=PROFILE,
        run=dataclasses.replace(config.run, model_max_output=1_000),
    )


def answers(request: ModelRequest) -> ScriptedReply:
    turn = assistant_turns(request)
    return ScriptedReply((TextPart(text=f"Answer {turn + 1}."),), "end", USAGE)


def waits_a_minute(request: ModelRequest) -> ScriptedReply:
    if assistant_turns(request) == 0:
        wait = ToolCallPart(id="c1", name="wait", arguments={"seconds": 60})
        return ScriptedReply((wait,), "tool_calls", USAGE)
    return ScriptedReply((TextPart(text="Waited."),), "end", USAGE)


def parts_for(
    config: Config,
    keyboard: Keyboard,
    screen: io.StringIO,
    errors: io.StringIO,
    choose: Choose = answers,
    answer: Answer = lambda command: ENDED_WELL,
) -> ChatParts:
    return chat_parts(
        config,
        ModelByRequest(choose),
        tooling_for(config, StubTools(SystemClock()), answer),
        keyboard.reader,
        screen,
        errors,
    )


def chat_with(
    tmp_path: Path,
    keyboard: Keyboard,
    session: str | None = None,
    choose: Choose = answers,
    person: Callable[[Screen], None] | None = None,
    answer: Answer = lambda command: ENDED_WELL,
    config: Config | None = None,
) -> tuple[int, str, str]:
    """Run one chat, with the person playing beside it, and give what it did."""
    screen, errors = Screen(), io.StringIO()
    playing = threading.Thread(target=person, args=(screen,)) if person else None
    if playing:
        playing.start()
    # A chat that never ends would hang the test run. Ending its input after
    # a bound makes it a failed test instead.
    timed_out = threading.Event()

    def end_input() -> None:
        timed_out.set()
        keyboard.close()

    watchdog = threading.Timer(CHAT_SECONDS, end_input)
    watchdog.start()
    try:
        config = config or config_in(tmp_path)
        code = chat(
            config,
            session,
            parts_for(config, keyboard, screen, errors, choose, answer),
        )
    finally:
        watchdog.cancel()
        keyboard.close()
        if playing:
            playing.join(CHAT_SECONDS)
        keyboard.done()
    assert not timed_out.is_set(), "the chat did not end by itself"
    return code, screen.getvalue(), errors.getvalue()


def test_a_first_start_makes_the_folder_and_answers_each_line(
    tmp_path: Path,
) -> None:
    keyboard = Keyboard()

    def person(screen: Screen) -> None:
        # Each line is typed once the answer before it is shown. A line typed
        # during a run joins that run, and gets no answer of its own.
        keyboard.type(b"Hello.\n")
        if screen.wait_for("Answer 1.\n> "):
            keyboard.type(b"Again.\n")
            screen.wait_for("Answer 2.\n> ")
        keyboard.close()

    code, screen, errors = chat_with(tmp_path, keyboard, person=person)
    assert (code, errors) == (0, "")
    folder = tmp_path / "store"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert (folder / "state.db").is_file()
    session = screen.splitlines()[0].removeprefix("Session ")
    assert screen == (
        f"Session {session}\n"
        "> [model call 1]\nAnswer 1.\n> "
        "[model call 1]\nAnswer 2.\n> "
    )


def test_a_new_session_is_started_and_its_id_printed(tmp_path: Path) -> None:
    code, screen, _ = chat_with(tmp_path, typed(b"Hello.\n"))
    assert code == 0
    session = screen.splitlines()[0].removeprefix("Session ")
    connection = open_database(tmp_path / "store" / "state.db", 5.0)
    assert not isinstance(connection, Failure)
    events = asyncio.run(SqliteEventLog(connection, {}, 10_000).read(session, 0))
    connection.close()
    assert not isinstance(events, Failure)
    assert [e.type for e in events if e.type in ("run_started", "run_end")] == [
        "run_started",
        "run_end",
    ]


def test_a_session_given_by_id_is_carried_on_with_no_session_line(
    tmp_path: Path,
) -> None:
    chat_with(tmp_path, typed(b"Hello.\n"), session="kitchen")
    code, screen, _ = chat_with(tmp_path, typed(b"Again.\n"), session="kitchen")
    assert code == 0
    assert "Session " not in screen
    # The model saw the first exchange, so this is its second answer.
    assert "Answer 2." in screen


def test_a_session_id_that_breaks_the_id_rule_is_refused(tmp_path: Path) -> None:
    code, screen, errors = chat_with(tmp_path, typed(b""), session="-rm")
    assert (code, screen) == (1, "")
    assert "breaks the id rule" in errors
    assert not (tmp_path / "store").exists()


def test_a_second_process_on_the_store_is_refused(tmp_path: Path) -> None:
    held = lock_store(tmp_path / "store")
    assert held is not None
    try:
        code, screen, errors = chat_with(tmp_path, typed(b"Hello.\n"))
    finally:
        os.close(held)
    assert (code, screen, errors) == (1, "", LOCK_HELD + "\n")
    assert not (tmp_path / "store" / "state.db").exists()
    assert LOCK_HELD == "Another lamplighter process is using this store."


def test_the_lock_is_free_again_once_the_chat_has_ended(tmp_path: Path) -> None:
    chat_with(tmp_path, typed(b""))
    held = lock_store(tmp_path / "store")
    assert held is not None
    os.close(held)


@dataclass
class Saved:
    """The stores of a chat's store folder, to fill before the chat starts."""

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    ledger: EffectLedger


def fill(tmp_path: Path, step: Callable[[Saved], Coroutine[Any, Any, None]]) -> None:
    folder = tmp_path / "store"
    folder.mkdir(mode=0o700)
    connection = open_database(folder / "state.db", 5.0)
    assert not isinstance(connection, Failure)
    clock = SystemClock()
    saved = Saved(
        SqliteEventLog(connection, {}, 10_000),
        SqliteInbox(connection, 10_000),
        SqliteOutbox(connection, clock, 10_000),
        SqliteEffectLedger(connection, clock, 10_000),
    )
    asyncio.run(step(saved))
    connection.close()


async def an_open_run(saved: Saved, session: str) -> None:
    started = NewEvent(type="run_started", v=1, payload={}, at=AT, run_id="r-1")
    assert await saved.log.append(session, [started], 0) == 1


async def unsettled_in_an_open_run(saved: Saved) -> None:
    await an_open_run(saved, "sess-b")
    begun = await saved.ledger.begin(action("sess-b"), None, None)
    assert not isinstance(begun, Failure)


async def unsettled_with_no_open_run(saved: Saved) -> None:
    begun = await saved.ledger.begin(action("sess-b"), None, None)
    assert not isinstance(begun, Failure)


@pytest.mark.parametrize(
    "unsettled", [unsettled_in_an_open_run, unsettled_with_no_open_run]
)
def test_an_action_whose_outcome_is_not_known_is_counted_and_the_chat_starts(
    tmp_path: Path, unsettled: Callable[[Saved], Coroutine[Any, Any, None]]
) -> None:
    fill(tmp_path, unsettled)
    code, screen, errors = chat_with(tmp_path, typed(b"Hello.\n"), session="sess-a")
    assert (code, errors) == (0, "")
    assert UNSETTLED.format(n=1) in screen.splitlines()
    assert "Answer 1." in screen
    assert UNSETTLED.format(n=1) == (
        "Actions whose outcome is not known: 1."
        " List them with: lamplighter store sessions"
    )


def test_the_first_command_runs_before_the_first_message_and_brings_on_the_wall(
    tmp_path: Path,
) -> None:
    seen: list[Command] = []

    def answer(command: Command) -> ExecResult | Failure:
        seen.append(command)
        return ENDED_WELL

    code, screen, _ = chat_with(tmp_path, typed(b"Hello.\n"), answer=answer)
    assert code == 0 and "Answer 1." in screen
    config = config_in(tmp_path)
    assert seen == [
        Command(
            argv=("/usr/bin/true",),
            cwd=config.tools.work_dir,
            env={},
            profile="read_only",
            wall_ms=5_000,
            capture_bytes=1_000,
        )
    ]
    assert not any("sandbox" in line for line in screen.splitlines())


def test_a_wall_that_is_down_is_said_and_the_chat_starts(tmp_path: Path) -> None:
    down = Failure("unavailable", "none", "The wall needs macOS.", "test")
    code, screen, errors = chat_with(
        tmp_path, typed(b"Hello.\n"), answer=lambda command: down
    )
    assert (code, errors) == (0, "")
    line = NO_WALL.format(message=down.message)
    assert line in screen.splitlines()
    assert line == (
        "The sandbox is not available: The wall needs macOS. Commands and"
        " searches will fail. File tools and web_fetch still work."
    )
    assert screen.index(line) < screen.index("Answer 1.")


def test_another_failure_of_the_first_command_is_not_called_a_wall_down(
    tmp_path: Path,
) -> None:
    refused = Failure("invalid_input", "none", "made-up", "test")
    code, screen, _ = chat_with(
        tmp_path, typed(b"Hello.\n"), answer=lambda command: refused
    )
    assert code == 0 and "sandbox" not in screen


def test_the_start_makes_the_tool_folders_private(tmp_path: Path) -> None:
    code, _, _ = chat_with(tmp_path, typed(b""))
    tools = config_in(tmp_path).tools
    assert code == 0
    for folder in (
        tools.work_dir,
        tools.scratch_dir,
        f"{tools.scratch_dir}/home",
        f"{tools.scratch_dir}/tmp",
    ):
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700, folder


def test_a_work_folder_reached_through_a_link_stops_the_start(tmp_path: Path) -> None:
    config = config_in(tmp_path)
    real = tmp_path / "real-work"
    real.mkdir()
    Path(config.tools.work_dir).symlink_to(real)
    code, screen, errors = chat_with(tmp_path, typed(b"Hello.\n"), config=config)
    assert (code, screen) == (1, "")
    assert errors == (
        THROUGH_A_LINK.format(
            setting="tools.work_dir", path=config.tools.work_dir, real=real
        )
        + "\n"
    )
    assert errors == (
        f"tools.work_dir is {config.tools.work_dir}, which is reached through a"
        f" symbolic link to {real}. Write the resolved path in the settings file.\n"
    )


async def waiting_work(saved: Saved) -> None:
    await an_open_run(saved, "sess-d")
    await saved.inbox.accept(inbound("b1", session="sess-b"))
    left = Outbound(
        out_id=out_id_for("sess-c", 4), session_id="sess-c", kind="reply", text="Hi."
    )
    assert await saved.outbox.enqueue(left) is None
    # Work for the session being opened is carried on, not listed.
    await saved.inbox.accept(inbound("a1", session="sess-a"))


def test_other_sessions_with_work_waiting_are_listed(tmp_path: Path) -> None:
    fill(tmp_path, waiting_work)
    code, screen, _ = chat_with(tmp_path, typed(b""), session="sess-a")
    assert code == 0
    listed = [line for line in screen.splitlines() if "unfinished work" in line]
    assert listed == [
        WAITING.format(id=other) for other in ("sess-b", "sess-c", "sess-d")
    ]
    assert listed[0] == (
        "Session sess-b has unfinished work."
        " Carry it on with: lamplighter chat --session sess-b"
    )
    assert "Answer 1." in screen


def test_a_missing_key_is_said_and_nothing_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = tmp_path / "lamplighter.toml"
    settings.write_text(
        SETTINGS.read_text().replace(
            'dir = "~/.lamplighter"', f'dir = "{tmp_path / "store"}"'
        )
    )
    assert main(["--config", str(settings), "chat"]) == 1
    assert capsys.readouterr().err == NO_KEY + "\n"
    assert NO_KEY == "ANTHROPIC_API_KEY is not set."
    assert not (tmp_path / "store").exists()


def press_ctrl_c() -> None:
    os.kill(os.getpid(), signal.SIGINT)


def test_ctrl_c_at_the_prompt_leaves_with_code_0(tmp_path: Path) -> None:
    def person(screen: Screen) -> None:
        if screen.wait_for("> "):
            press_ctrl_c()

    # The input stays open, as in a terminal, so only Ctrl-C ends the chat.
    code, screen, errors = chat_with(tmp_path, Keyboard(), person=person)
    assert (code, errors) == (0, "")
    assert screen.endswith("> ")


def test_ctrl_c_during_a_tool_call_stops_the_run_and_the_prompt_comes_back(
    tmp_path: Path,
) -> None:
    keyboard = Keyboard()
    seen: list[bool] = []

    def person(screen: Screen) -> None:
        keyboard.type(b"Wait a minute.\n")
        if screen.wait_for("[tool wait]"):
            press_ctrl_c()
            seen.append(screen.wait_for("[Stopped.]\n> "))
        keyboard.close()

    code, screen, _ = chat_with(
        tmp_path, keyboard, choose=waits_a_minute, person=person
    )
    assert code == 0
    assert seen == [True]
    assert "Waited." not in screen


def test_the_end_of_input_during_a_run_still_shows_the_reply(tmp_path: Path) -> None:
    code, screen, _ = chat_with(tmp_path, typed(b"Hello."))
    assert code == 0
    assert "Answer 1." in screen


def test_the_run_spec_carries_the_wall_and_its_id_covers_all_six(
    tmp_path: Path,
) -> None:
    config = config_in(tmp_path)
    tooling = tooling_for(config, StubTools(SystemClock()))(InMemoryBlobStore(10))
    spec = run_spec(config, tooling)
    assert spec.wall == {"adapter": "fake"}
    assert spec.config_id == config_id(
        config.model,
        config.prices,
        config.run,
        tooling.tools.list_tools(),
        dataclasses.asdict(config.tools),
        {"adapter": "fake"},
    )
    moved = dataclasses.replace(
        config, tools=dataclasses.replace(config.tools, work_dir="/made-up/moved")
    )
    assert run_spec(moved, tooling).config_id != spec.config_id


def test_main_builds_the_seven_built_in_tools_behind_the_wall(tmp_path: Path) -> None:
    config = config_in(tmp_path)
    Path(config.tools.work_dir).mkdir()
    built = builtin_tooling(config, SystemClock())(InMemoryBlobStore(10))
    assert [entry.spec.name for entry in built.tools.list_tools()] == [
        "edit_file",
        "read_file",
        "read_output",
        "search_files",
        "shell",
        "web_fetch",
        "write_file",
    ]
    assert built.executor.wall()["adapter"] == "seatbelt"


def test_the_chat_starts_on_the_real_tools_before_their_folders_exist(
    tmp_path: Path,
) -> None:
    # The built-in tools open the work folder when they are made, so the
    # folders must be made first. Off macOS the wall is down, and says so.
    config = config_in(tmp_path)
    screen, errors, keyboard = Screen(), io.StringIO(), typed(b"Hello.\n")
    parts = chat_parts(
        config,
        ModelByRequest(answers),
        builtin_tooling(config, SystemClock()),
        keyboard.reader,
        screen,
        errors,
    )
    try:
        code = chat(config, None, parts)
    finally:
        keyboard.close()
        keyboard.done()
    assert (code, errors.getvalue()) == (0, "")
    assert "Answer 1." in screen.getvalue()
    if sys.platform != "darwin":
        assert "The sandbox is not available: The wall needs macOS" in (
            screen.getvalue()
        )
