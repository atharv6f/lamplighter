"""Builds the ledger's record of one change from the call that asks for it."""

import hashlib

from lamplighter.core.run_view import OpenCall
from lamplighter.ports import (
    Action,
    ActionClass,
    JsonObject,
    canonical_json,
    effect_key,
    saved_json,
)


def action_for(
    session_id: str, call: OpenCall, action_class: ActionClass, target: str
) -> Action:
    """Give the action of a call, from where it came from and what it changes.

    Every field is worked out from the saved reply, the class and the
    target, so a pass after a kill builds the same action and `begin` finds
    the row it saved. Raises ValueError for a call whose arguments did not
    parse, because such a call is never planned.
    """
    arguments = call.call.arguments
    if arguments is None:
        raise ValueError("a call whose arguments did not parse has no action")
    return Action(
        session_id=session_id,
        reply_seq=call.reply_seq,
        call_index=call.place,
        attempt=0,
        tool=call.call.name,
        args=arguments,
        action_class=action_class,
        target=target,
        effect_key=effect_key(session_id, call.reply_seq, call.place, 0),
        action_hash=action_hash(call.call.name, arguments, target),
        outward=False,
    )


def action_hash(tool: str, arguments: JsonObject, target: str) -> str:
    """Give the hash that an approval of this action will be bound to.

    The arguments go in as their saved text, so a float in them is hashed
    as it was saved.
    """
    text = canonical_json(["action", tool, saved_json(arguments), target])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
