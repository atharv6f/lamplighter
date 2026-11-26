"""The port the core calls to list the tools, run reads, and plan and apply changes."""

from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.effect_ledger import ActionClass
from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject
from lamplighter.ports.model import ToolSpec

type ToolEffect = Literal["none", "done"]


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """The harness's record of one tool: what the model is told, and more.

    `spec` is all the model is told. `action_class` says whether a call
    reads or changes something, and comes from this repo's own code, never
    from the tool's word. `output_cap` is the most characters of the tool's
    text that reach the model. `taints` says the text comes from outside
    this machine.
    """

    spec: ToolSpec
    action_class: ActionClass
    output_cap: int
    taints: bool


@dataclass(frozen=True, slots=True)
class Plan:
    """What a tool says before a change: its target, a probe, an undo recipe.

    `target` is one short text that depends only on the tool and the
    arguments. `probe` is the recipe for checking later whether the change
    happened, and `undo` the recipe for putting things back. Either is None
    when nothing can do that job. Both are plain data the ledger saves.
    """

    target: str
    probe: JsonObject | None
    undo: JsonObject | None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool gave back: a text, whether it says something went wrong, and
    whether the call changed anything.

    `effect` is "none" when the call changed nothing, and "done" when it
    changed something or may have.
    """

    text: str
    is_error: bool
    effect: ToolEffect


class ToolProvider(Protocol):
    """The tools the model may call.

    Callers can rely on this:

    - `list_tools` gives the same entries, sorted by name, every time it is
      called in one process. Every schema is an object schema that uses only
      the keywords the core's schema check knows.
    - `call` is for a tool whose class is "read". It leaves the world as it
      found it, so it may run twice. Its result has `effect` "none".
    - `plan` and `apply` are for a tool of any other class. `plan` changes
      nothing in the world, though it may save a blob. It gives a Plan, or a
      ToolResult with `effect` "none" when nothing will be changed: a
      refusal, with `is_error` set, or a change already in place, without.
    - The target of a plan depends only on the name and the arguments.
    - `apply` carries out exactly the plan it is given. If the world no
      longer matches the plan, it changes nothing and says so in a result
      with `effect` "none". It says truly what it did: `effect` is "none"
      only when the plan's target is as it was. A Failure from `apply` sets
      its own `effect` the same way, or "unknown" when the tool cannot say.
    - `apply` is called at most once for one effect, and never repeats
      itself.
    - A tool cancelled while it changes something answers with its true
      result when it can. One that cannot say what happened gives no value.
    - `check` only reads. It gives True when the probe shows the change in
      place, False when it shows the change is not, and None when it cannot
      tell. It gives None for a probe of a kind it does not know, for a
      probe made for another place than the one this provider works in, and
      for a probe whose `apply` this provider has started and not ended.
    - No method raises for bad arguments. A mistake the model can fix is a
      ToolResult with `is_error` set. An expected failure is a Failure.
    - The UTF-8 form of a tool's text is at most 26,214,400 bytes.
    - A provider never asks anyone anything, never decides whether a call is
      allowed, and never writes to the log or the ledger.
    """

    def list_tools(self) -> tuple[ToolEntry, ...]:
        """Give the entry of every tool, sorted by name."""
        ...

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run the read `name` with these arguments, and give what it said."""
        ...

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        """Say what a change would touch, how to check it, and how to undo it."""
        ...

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        """Carry out exactly this plan, or change nothing and say why."""
        ...

    async def check(self, probe: JsonObject) -> bool | None:
        """Say whether the change a probe describes is in place, or None."""
        ...
