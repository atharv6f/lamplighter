"""The real chat process in a real terminal, with a person typing and pressing keys.

The terminal is a pty: a pair of file descriptors that behaves like a
terminal, so the test can type, press Ctrl-C and Ctrl-D, and read the
screen. These tests check what happens, not how fast. The one-second rule
is measured by `scripts/stop_timing.py` on the Mac.
"""

import os
import signal
import time
from pathlib import Path

from lamplighter.app.chat import LEAVING, LOCK_HELD
from tests.group_kill import kill_group
from tests.pty_terminal import CTRL_C, CTRL_D, SECONDS, Terminal, piped


def test_ctrl_c_during_a_tool_stops_the_run_and_gives_the_prompt_back(
    tmp_path: Path,
) -> None:
    terminal = Terminal(tmp_path, "answers")
    try:
        terminal.wait_for("> ")
        terminal.type(b"Wait a minute.\n")
        at = terminal.wait_for("[tool wait]")
        started = time.monotonic()
        terminal.type(CTRL_C)
        terminal.wait_for("> ", terminal.wait_for("[Stopped.]", at))
        assert time.monotonic() - started < SECONDS
        terminal.type(CTRL_D)
        assert terminal.exit_code() == 0
        assert "Waited." not in terminal.seen
    finally:
        terminal.close()


def test_ctrl_c_during_a_slow_model_call_stops_the_run(tmp_path: Path) -> None:
    terminal = Terminal(tmp_path, "slow_model")
    try:
        terminal.wait_for("> ")
        terminal.type(b"Hello.\n")
        at = terminal.wait_for("[model call 1]")
        terminal.type(CTRL_C)
        terminal.wait_for("> ", terminal.wait_for("[Stopped.]", at))
        terminal.type(CTRL_D)
        assert terminal.exit_code() == 0
    finally:
        terminal.close()


def test_a_second_ctrl_c_leaves_at_once_and_a_new_start_closes_the_run(
    tmp_path: Path,
) -> None:
    terminal = Terminal(tmp_path, "stubborn")
    try:
        session = terminal.session()
        terminal.type(b"Wait a minute.\n")
        terminal.wait_for("[tool wait]")
        terminal.type(CTRL_C)
        # Two signals that come together may reach the process as one, as
        # two presses by a person never do.
        time.sleep(0.2)
        terminal.type(CTRL_C)
        assert terminal.exit_code() == 130
        assert LEAVING in terminal.seen
    finally:
        terminal.close()
    # The same settings again, or the run would be ended as one whose
    # settings changed.
    again = Terminal(tmp_path, "stubborn", session)
    try:
        # The first press saved a stop before the exit, so the run it was
        # meant for is closed as stopped, and the model is not asked again.
        again.wait_for("> ", again.wait_for("[Stopped.]"))
        again.type(CTRL_D)
        assert again.exit_code() == 0
        assert "[model call" not in again.seen
    finally:
        again.close()


def test_ctrl_c_works_while_a_run_cut_off_by_a_kill_is_carried_on(
    tmp_path: Path,
) -> None:
    terminal = Terminal(tmp_path, "slow_model")
    try:
        session = terminal.session()
        terminal.type(b"Hello.\n")
        terminal.wait_for("[model call 1]")
    finally:
        terminal.close()
    again = Terminal(tmp_path, "slow_model", session)
    try:
        # No line has been read yet: the run goes on from the store at start.
        at = again.wait_for("[model call 2]")
        again.type(CTRL_C)
        again.wait_for("> ", again.wait_for("[Stopped.]", at))
        again.type(CTRL_D)
        assert again.exit_code() == 0
    finally:
        again.close()


def test_ctrl_d_during_a_run_still_shows_the_reply(tmp_path: Path) -> None:
    terminal = Terminal(tmp_path, "answers")
    try:
        terminal.wait_for("> ")
        terminal.type(b"Wait a second.\n")
        terminal.wait_for("[tool wait]")
        terminal.type(CTRL_D)
        assert terminal.exit_code() == 0
        assert "Waited a second." in terminal.seen
    finally:
        terminal.close()


def test_ctrl_c_and_ctrl_d_at_the_prompt_each_leave_with_code_0(
    tmp_path: Path,
) -> None:
    for key in (CTRL_C, CTRL_D):
        terminal = Terminal(tmp_path, "answers")
        try:
            terminal.wait_for("> ")
            terminal.type(key)
            assert terminal.exit_code() == 0
        finally:
            terminal.close()


def test_ctrl_c_during_a_run_on_piped_input_stops_it_and_leaves_with_0(
    tmp_path: Path,
) -> None:
    child = piped(tmp_path, "answers", b"Wait a minute.\n")
    try:
        assert child.stdout is not None
        seen = b""
        while b"[tool wait]" not in seen:
            line = child.stdout.readline()
            assert line, seen
            seen += line
        os.kill(child.pid, signal.SIGINT)
        rest, errors = child.communicate(timeout=SECONDS)
        assert child.returncode == 0, errors
        assert b"[Stopped.]" in rest
    finally:
        kill_group(child)


def test_a_piped_last_line_with_no_line_break_is_answered(tmp_path: Path) -> None:
    child = piped(tmp_path, "answers", b"Hello.")
    shown, errors = child.communicate(timeout=SECONDS)
    assert child.returncode == 0, errors
    assert b"Hi there." in shown


def test_a_second_process_on_the_store_is_refused_at_once(tmp_path: Path) -> None:
    terminal = Terminal(tmp_path, "answers")
    try:
        terminal.wait_for("> ")
        second = piped(tmp_path, "answers", b"Hello.\n")
        shown, errors = second.communicate(timeout=SECONDS)
        assert second.returncode == 1
        assert (shown, errors.decode()) == (b"", LOCK_HELD + "\n")
        terminal.type(CTRL_D)
        assert terminal.exit_code() == 0
    finally:
        terminal.close()
