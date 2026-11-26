"""Times the chat on this machine: Ctrl-C to prompt, a rebuild, and the longest line.

Run it from the repo's root with `uv run python -m scripts.chat_timing`.
It writes only inside new temporary folders, which it removes, and it calls
no model: the chat runs with the made-up model the tests use.

1. Ctrl-C to prompt. The chat runs in a pty, as in a real terminal. Ten
   times a tool is stopped, and ten times a model call. The time is from
   the Ctrl-C to the prompt that follows "[Stopped.]". The rule is under
   one second.
2. Rebuilding the transcript of a session of about 5,000 events, made of
   runs of 40 steps. Above 250 ms, the transcript would have to be kept
   between the steps of one run.
3. The longest line the terminal passes on: one line of 10,000 bytes is
   typed, and the made-up model answers with how many bytes it got. Where
   a terminal drops a line that long, as macOS does, the longest line that
   still arrives is found by halving, and the script says whether Ctrl-C
   still works after the dropped line.
"""

import asyncio
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from lamplighter.adapters import ScriptedReply
from lamplighter.core import rebuild_messages, run_turn
from lamplighter.ports import Failure, ModelRequest, ToolCallPart, Usage
from tests.loop_kit import in_memory
from tests.model_by_request import ModelByRequest
from tests.pty_terminal import CTRL_C, CTRL_D, Terminal
from tests.run_history import SESSION, SPEC, inbound

TRIES = 10
STEPS_PER_RUN = 40
EVENTS = 5_000
LONG_LINE = 10_000
# A line that arrives is answered in milliseconds. One that does not, never.
LINE_WAIT_S = 3.0
USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
# Runs that end at their step limit, and at no other.
FORTY_STEPS = replace(
    SPEC,
    max_steps=STEPS_PER_RUN,
    max_tokens=10**12,
    max_micro_dollars=10**12,
    max_seconds=10**9,
)


def stop_to_prompt(behaviour: str, typed: bytes, started: str) -> float:
    """Give the seconds from a Ctrl-C during a call to the prompt after it."""
    folder = Path(tempfile.mkdtemp())
    terminal = Terminal(folder / "store", behaviour)
    try:
        terminal.wait_for("> ")
        terminal.type(typed)
        at = terminal.wait_for(started)
        pressed = time.monotonic()
        terminal.type(CTRL_C)
        terminal.wait_for("> ", terminal.wait_for("[Stopped.]", at))
        took = time.monotonic() - pressed
        terminal.type(CTRL_D)
        terminal.exit_code()
        return took
    finally:
        terminal.close()
        shutil.rmtree(folder)


def runaway(request: ModelRequest) -> ScriptedReply:
    """Call `current_time` at every step, so each run goes to its step limit."""
    asked = ToolCallPart(
        id=f"call-{len(request.messages)}", name="current_time", arguments={}
    )
    return ScriptedReply((asked,), "tool_calls", USAGE)


async def rebuild_time() -> tuple[int, float]:
    """Build a long session in runs of 40 steps, then time one rebuild."""
    loop = in_memory(ModelByRequest(runaway))
    made = 0
    while True:
        events = await loop.log.read(SESSION, 0)
        assert not isinstance(events, Failure)
        if len(events) >= EVENTS:
            break
        made += 1
        await loop.inbox.accept(inbound(f"m{made}", f"Run number {made}."))
        while await run_turn(SESSION, FORTY_STEPS, loop.deps) is not None:
            pass
    times: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        rebuilt = rebuild_messages(events)
        times.append(time.perf_counter() - started)
        assert not isinstance(rebuilt, Failure)
    return len(events), statistics.median(times)


def arrived(length: int) -> tuple[int | None, int | None]:
    """Type one line of `length` bytes, then Ctrl-C if it did not arrive.

    Give how many bytes the chat got, or None, and, when the line did not
    arrive, the exit code after the Ctrl-C, or None if it had no effect.
    """
    folder = Path(tempfile.mkdtemp())
    terminal = Terminal(folder / "store", "lengths")
    try:
        at = terminal.wait_for("> ")
        terminal.type(b"x" * length + b"\n")
        end = terminal.find(" bytes.", at, LINE_WAIT_S)
        if end is not None:
            got = re.findall(r"Got (\d+) bytes\.", terminal.seen[:end])
            return int(got[-1]), None
        terminal.type(CTRL_C)
        try:
            return None, terminal.child.wait(LINE_WAIT_S)
        except subprocess.TimeoutExpired:
            return None, None
    finally:
        terminal.close()
        shutil.rmtree(folder)


def longest_line() -> str:
    """Say what a typed line of 10,000 bytes became, and the longest that arrives."""
    got, code = arrived(LONG_LINE)
    if got is not None:
        return f"A typed line of {LONG_LINE} bytes arrived as {got} bytes"
    after = "had no effect" if code is None else f"left with code {code}"
    # The longest length known to arrive, and the shortest known not to.
    fits, too_long = 1, LONG_LINE
    while too_long - fits > 1:
        middle = (fits + too_long) // 2
        if arrived(middle)[0] is None:
            too_long = middle
        else:
            fits = middle
    return (
        f"A typed line of {LONG_LINE} bytes never arrived, and Ctrl-C after it"
        f" {after}. The longest line that arrived: {fits} bytes"
    )


def main() -> None:
    """Print the three numbers."""
    tools = [
        stop_to_prompt("answers", b"Wait a minute.\n", "[tool wait]")
        for _ in range(TRIES)
    ]
    models = [
        stop_to_prompt("slow_model", b"Hello.\n", "[model call 1]")
        for _ in range(TRIES)
    ]
    for name, times in (("a tool", tools), ("a model call", models)):
        print(
            f"Ctrl-C to prompt, stopping {name}, {TRIES} times:"
            f" median {statistics.median(times) * 1000:.0f} ms,"
            f" largest {max(times) * 1000:.0f} ms"
        )
    events, rebuild = asyncio.run(rebuild_time())
    print(f"Rebuilding a transcript of {events} events: {rebuild * 1000:.1f} ms")
    print(longest_line())


if __name__ == "__main__":
    main()
