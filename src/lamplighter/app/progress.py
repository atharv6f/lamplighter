"""The observers that print one line before each model call and each tool call."""

from collections.abc import Callable

from lamplighter.core import ModelCallStarted, Observers, ToolCallStarted


def progress_lines(show_line: Callable[[str], None]) -> Observers:
    """Give observers that print `[model call N]` and `[tool NAME]`."""

    async def model_call(started: ModelCallStarted) -> None:
        show_line(f"[model call {started.step}]")

    async def tool_call(started: ToolCallStarted) -> None:
        show_line(f"[tool {started.call.name}]")

    return Observers(before_model=(model_call,), before_tool=(tool_call,))
