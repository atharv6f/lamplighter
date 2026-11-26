"""The port the core calls to list the tools and run one, in its smallest form."""

from dataclasses import dataclass
from typing import Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject
from lamplighter.ports.model import ToolSpec


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool gave back: one text, and whether it says something went wrong."""

    text: str
    is_error: bool


class ToolProvider(Protocol):
    """The tools the model may call.

    Callers can rely on this:

    - `list_tools` gives the same specs every time, sorted by name.
    - `call` with a listed name gives a ToolResult or a Failure, and never
      raises because of the arguments. A mistake the model can fix, such as
      a missing argument, is a ToolResult with `is_error` set.
    - `call` can be cancelled while it runs.
    """

    def list_tools(self) -> tuple[ToolSpec, ...]:
        """Give the spec of every tool, sorted by name."""
        ...

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run the tool `name` with these arguments, and give what it said."""
        ...
