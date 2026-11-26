"""Checks the input of a ledger call, so every ledger refuses the same things."""

from collections.abc import Mapping

from lamplighter.ports.effect_ledger import Action, EffectResult, Outcome
from lamplighter.ports.event_check import invalid
from lamplighter.ports.failure import Failure
from lamplighter.ports.json_text import saved_size
from lamplighter.ports.json_value import JsonObject
from lamplighter.ports.value_rules import is_hash, is_id


def check_begin(
    action: Action,
    probe: JsonObject | None,
    undo: JsonObject | None,
    max_record_bytes: int,
    source: str,
) -> Failure | None:
    """Give the Failure for a `begin` no ledger may accept, or None."""
    problem = _action_problem(action)
    if problem is not None:
        return invalid(problem, source)
    named = {"action": action, "probe": probe, "undo": undo}
    return _unsaveable(named, source) or _too_large(named, max_record_bytes, source)


def check_finish(
    result: EffectResult, max_record_bytes: int, source: str
) -> Failure | None:
    """Give the Failure for a `finish` no ledger may accept, or None."""
    named = {"result": result}
    return _unsaveable(named, source) or _too_large(named, max_record_bytes, source)


def check_resolve(
    outcome: Outcome, max_record_bytes: int, source: str
) -> Failure | None:
    """Give the Failure for a `resolve` no ledger may accept, or None."""
    named = {"outcome": outcome}
    return _unsaveable(named, source) or _too_large(named, max_record_bytes, source)


def unknown_effect(effect_id: int, source: str) -> Failure:
    """Build the failure for an effect id the ledger never gave out."""
    return invalid(f"No effect has the id {effect_id}.", source)


def _action_problem(action: Action) -> str | None:
    if not is_id(action.session_id):
        return "The action's session id breaks the id rule."
    if min(action.reply_seq, action.call_index, action.attempt) < 0:
        return "reply_seq, call_index and attempt must be 0 or more."
    if not (is_hash(action.effect_key) and is_hash(action.action_hash)):
        return "effect_key and action_hash must be hashes."
    if action.v < 1:
        return "v must be 1 or more."
    return None


def _unsaveable(named: Mapping[str, object], source: str) -> Failure | None:
    for name, value in named.items():
        try:
            saved_size(value)
        except ValueError:
            return invalid(f"The {name} cannot be saved as JSON.", source)
    return None


def _too_large(
    named: Mapping[str, object], max_record_bytes: int, source: str
) -> Failure | None:
    for name, value in named.items():
        if value is not None and saved_size(value) > max_record_bytes:
            return Failure(
                kind="too_large",
                effect="none",
                message=f"The {name} is over {max_record_bytes} bytes.",
                source=source,
            )
    return None
