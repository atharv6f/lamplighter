"""The progress lines: one before each model call and one before each tool call."""

import asyncio

from lamplighter.app.progress import progress_lines
from tests.test_observers import plain_run


def test_a_line_is_printed_before_each_model_call_and_each_tool_call() -> None:
    lines: list[str] = []

    def show_line(text: str) -> None:
        lines.append(text)

    asyncio.run(plain_run(progress_lines(show_line)))
    # The run's first reply calls two tools and its second calls none.
    assert lines == [
        "[model call 1]",
        "[tool current_time]",
        "[tool wait]",
        "[model call 2]",
        "[model call 3]",
    ]
