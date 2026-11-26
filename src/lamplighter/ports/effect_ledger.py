"""The port for the record of what was done to the world, and its records."""

from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

type ActionClass = Literal[
    "read", "reversible_write", "compensable", "irreversible", "external_message"
]

type EffectState = Literal[
    "intended", "done", "failed", "unknown", "resolved", "undone"
]


@dataclass(frozen=True, slots=True)
class Action:
    """One run of one tool call, as the ledger records it before it runs.

    The first four fields say where the call came from: its session, the seq
    of the model reply that asked for it, its place in that reply from 0, and
    which run of the call this is, from 0. `effect_key` is worked out from
    those four. `action_hash` is what an approval is bound to. The ledger
    saves both and checks neither.
    """

    session_id: str
    reply_seq: int
    call_index: int
    attempt: int
    tool: str
    args: JsonObject
    action_class: ActionClass
    target: str
    effect_key: str
    action_hash: str
    outward: bool
    v: int = 1


@dataclass(frozen=True, slots=True)
class EffectResult:
    """What a tool gave back, saved by `finish`.

    `undo` is the undo recipe when it is only known after the action, and
    None keeps the one `begin` saved. For "unknown", `output` holds whatever
    is known, such as why the tool timed out.
    """

    outcome: Literal["done", "failed", "unknown"]
    output: JsonObject
    undo: JsonObject | None = None
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """How an effect in "unknown" was settled, and what showed it."""

    happened: bool
    settled_by: Literal["probe", "check", "user"]
    evidence: JsonObject


@dataclass(frozen=True, slots=True)
class Effect:
    """The ledger's row for one action. Its action never changes once saved.

    `probe` and `undo` are recipes the ledger saves and never reads. Each
    carries its own kind and version. The three times are stamped by the
    ledger from its clock.
    """

    effect_id: int
    effect_key: str
    action: Action
    state: EffectState
    probe: JsonObject | None
    undo: JsonObject | None
    result: EffectResult | None
    resolution: Outcome | None
    begun_at: str
    finished_at: str | None
    resolved_at: str | None
    v: int = 1


@dataclass(frozen=True, slots=True)
class Begun:
    """What `begin` gives: the row as it now stands, and whether this call made it."""

    effect: Effect
    is_new: bool


class EffectLedger(Protocol):
    """The record of every action in the world: its intent, then how it ended.

    Callers can rely on this:

    - `begin` returns only after the intent is forced to the drive, so a tool
      that starts after `begin` returns always has its intent saved. After a
      kill between `begin` and `finish`, `unresolved` lists the effect.
    - `begin` with a key already saved and the same action changes nothing
      and returns the saved row, which is not new. Its `probe` and `undo`
      arguments are then ignored. A different action under a saved key is
      `conflict`. "The same" is judged after both are read back from their
      saved form, so a field that newer code added cannot make them differ.
    - `finish` moves an effect from "intended" to the outcome of its result,
      and `resolve` moves one from "unknown" to "resolved". Each is a no-op
      when the same value is already saved, and `conflict` for any other move.
    - An undo recipe is saved at `begin` or at `finish`, and never later.
    - An effect id the ledger never gave out is `invalid_input`. Any saved
      value over the size limit is `too_large`, and nothing is written.
    - `unresolved` gives every effect in "intended" or "unknown", oldest first.
    """

    async def begin(
        self, action: Action, probe: JsonObject | None, undo: JsonObject | None
    ) -> Begun | Failure:
        """Save the intent to run an action, or find the one already saved."""
        ...

    async def finish(self, effect_id: int, result: EffectResult) -> None | Failure:
        """Save how an intended action ended."""
        ...

    async def resolve(self, effect_id: int, outcome: Outcome) -> None | Failure:
        """Save how an effect whose outcome was unknown was settled."""
        ...

    async def unresolved(self) -> tuple[Effect, ...] | Failure:
        """Give every effect that is intended or unknown, oldest first."""
        ...
