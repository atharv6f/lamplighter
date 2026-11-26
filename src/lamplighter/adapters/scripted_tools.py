"""Tools that run functions a test gives, and note which calls started and ended."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from lamplighter.ports import Failure, JsonObject, ToolResult, ToolSpec

_SOURCE = "ToolProvider/scripted"

type ToolRun = Callable[[JsonObject], Awaitable[ToolResult | Failure]]


@dataclass(frozen=True, slots=True)
class ScriptedTool:
    """One tool: its spec, the function that answers it, and how it takes a cancel.

    A tool with `ignores_cancel` set goes on to its end when it is
    cancelled, as a badly made tool might.
    """

    spec: ToolSpec
    run: ToolRun
    ignores_cancel: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallNote:
    """One call a scripted tool saw: its name and its arguments."""

    name: str
    arguments: JsonObject


@dataclass
class _Notes:
    started: list[ToolCallNote] = field(default_factory=list[ToolCallNote])
    finished: list[ToolCallNote] = field(default_factory=list[ToolCallNote])


class ScriptedTools:
    """Runs the functions a test gave. Satisfies ToolProvider.

    `started` and `finished` list the calls in the order they began and
    ended, so a test can see that a call never ran, or ran once.
    """

    def __init__(self, tools: Sequence[ScriptedTool]) -> None:
        """Take the tools, in any order."""
        self._tools = {tool.spec.name: tool for tool in tools}
        self._notes = _Notes()

    @property
    def started(self) -> tuple[ToolCallNote, ...]:
        """Every call that began, in order."""
        return tuple(self._notes.started)

    @property
    def finished(self) -> tuple[ToolCallNote, ...]:
        """Every call that ran to its end, in order."""
        return tuple(self._notes.finished)

    def list_tools(self) -> tuple[ToolSpec, ...]:
        """Give every spec, sorted by name."""
        return tuple(self._tools[name].spec for name in sorted(self._tools))

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run the named tool's function, and note the start and the end."""
        tool = self._tools.get(name)
        if tool is None:
            return Failure(
                kind="invalid_input",
                effect="none",
                message=f"There is no tool named {name}.",
                source=_SOURCE,
            )
        note = ToolCallNote(name=name, arguments=arguments)
        self._notes.started.append(note)
        if tool.ignores_cancel:
            result = await _past_any_cancel(tool.run(arguments))
        else:
            result = await tool.run(arguments)
        self._notes.finished.append(note)
        return result


async def _past_any_cancel[T](work: Awaitable[T]) -> T:
    # The work runs as a task of its own, which a cancel of this one does not
    # reach. Each cancel is swallowed, which is the badly made tool's fault
    # this fake exists to show.
    task = asyncio.ensure_future(work)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
