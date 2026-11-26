"""An effect ledger kept in memory, for tests. It refuses what the real one does."""

import dataclasses
from typing import cast

from lamplighter.ports import (
    Action,
    Begun,
    Clock,
    Effect,
    EffectResult,
    Failure,
    JsonObject,
    Outcome,
    as_saved,
    check_begin,
    check_finish,
    check_resolve,
    read_saved_json,
    saved_json,
    unknown_effect,
    utc_text,
)

_SOURCE = "EffectLedger/in_memory"


class InMemoryEffectLedger:
    """Keeps every effect in a dict by its id. Satisfies EffectLedger."""

    def __init__(self, clock: Clock, max_record_bytes: int) -> None:
        self._clock = clock
        self._max_record_bytes = max_record_bytes
        self._by_id: dict[int, Effect] = {}
        self._id_by_key: dict[str, int] = {}

    async def begin(
        self, action: Action, probe: JsonObject | None, undo: JsonObject | None
    ) -> Begun | Failure:
        """Save the intent to run an action, or find the one already saved."""
        refused = check_begin(action, probe, undo, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        saved_action = as_saved(Action, action)
        if action.effect_key in self._id_by_key:
            effect = self._by_id[self._id_by_key[action.effect_key]]
            if effect.action != saved_action:
                return _conflict("A different action is saved under this key.")
            return Begun(effect=effect, is_new=False)
        effect = Effect(
            effect_id=len(self._by_id) + 1,
            effect_key=action.effect_key,
            action=saved_action,
            state="intended",
            probe=_plain(probe),
            undo=_plain(undo),
            result=None,
            resolution=None,
            begun_at=utc_text(self._clock.now()),
            finished_at=None,
            resolved_at=None,
        )
        self._by_id[effect.effect_id] = effect
        self._id_by_key[effect.effect_key] = effect.effect_id
        return Begun(effect=effect, is_new=True)

    async def finish(self, effect_id: int, result: EffectResult) -> None | Failure:
        """Save how an intended action ended."""
        refused = check_finish(result, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        effect = self._by_id.get(effect_id)
        if effect is None:
            return unknown_effect(effect_id, _SOURCE)
        saved_result = as_saved(EffectResult, result)
        if effect.state != "intended":
            if effect.result == saved_result:
                return None
            return _conflict(f"The effect is {effect.state} with another result.")
        self._by_id[effect_id] = dataclasses.replace(
            effect,
            state=saved_result.outcome,
            result=saved_result,
            undo=effect.undo if saved_result.undo is None else saved_result.undo,
            finished_at=utc_text(self._clock.now()),
        )
        return None

    async def resolve(self, effect_id: int, outcome: Outcome) -> None | Failure:
        """Save how an effect whose outcome was unknown was settled."""
        refused = check_resolve(outcome, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        effect = self._by_id.get(effect_id)
        if effect is None:
            return unknown_effect(effect_id, _SOURCE)
        saved_outcome = as_saved(Outcome, outcome)
        if effect.state == "resolved" and effect.resolution == saved_outcome:
            return None
        if effect.state != "unknown":
            return _conflict(f"Only an unknown effect is resolved, not {effect.state}.")
        self._by_id[effect_id] = dataclasses.replace(
            effect,
            state="resolved",
            resolution=saved_outcome,
            resolved_at=utc_text(self._clock.now()),
        )
        return None

    async def unresolved(self) -> tuple[Effect, ...] | Failure:
        """Give every effect that is intended or unknown, oldest first."""
        return tuple(
            effect
            for _, effect in sorted(self._by_id.items())
            if effect.state in ("intended", "unknown")
        )


def _plain(recipe: JsonObject | None) -> JsonObject | None:
    if recipe is None:
        return None
    return cast(JsonObject, read_saved_json(saved_json(recipe)))


def _conflict(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)
