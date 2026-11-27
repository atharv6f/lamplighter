"""The ledger's action for a call, with its key and hash, against literal values."""

import pytest

from lamplighter.core.action import action_for, action_hash
from lamplighter.core.run_view import OpenCall
from lamplighter.ports import Action, ToolCallPart

# The SHA-256 of ["action","put","{\"name\":\"tea\",\"text\":\"mint\"}","tea"].
PUT_HASH = "c2953f0655e7633f6fa10c1982b290dae54cdaa084edf8397a33b1e3e1f09ce4"
# The SHA-256 of ["effect","sess-a",4,1,0].
PUT_KEY = "8aa9ad3677aeb53f521fefee4a302431412b77c2798540a299997b59a80bab45"
ARGUMENTS = {"name": "tea", "text": "mint"}


def open_call(arguments: dict[str, str] | None = ARGUMENTS) -> OpenCall:
    return OpenCall(
        call=ToolCallPart(id="c1", name="put", arguments=arguments),
        index=3,
        reply_id="r" * 64,
        reply_seq=4,
        place=1,
        intents=(),
        cut=False,
    )


def test_the_action_of_a_call_is_built_from_where_it_came_from() -> None:
    assert action_for("sess-a", open_call(), "reversible_write", "tea") == Action(
        session_id="sess-a",
        reply_seq=4,
        call_index=1,
        attempt=0,
        tool="put",
        args=ARGUMENTS,
        action_class="reversible_write",
        target="tea",
        effect_key=PUT_KEY,
        action_hash=PUT_HASH,
        outward=False,
    )


def test_the_action_hash_holds_the_tool_the_saved_arguments_and_the_target() -> None:
    assert action_hash("put", ARGUMENTS, "tea") == PUT_HASH
    assert action_hash("put", ARGUMENTS, "jam") != PUT_HASH
    assert action_hash("put", {"name": "tea", "text": "mint!"}, "tea") != PUT_HASH


def test_a_call_whose_arguments_did_not_parse_has_no_action() -> None:
    with pytest.raises(ValueError):
        action_for("sess-a", open_call(None), "reversible_write", "tea")
