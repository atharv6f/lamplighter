"""The examples here are pinned. A change to a formula would orphan saved rows."""

import hashlib

from lamplighter.ports import (
    canonical_json,
    effect_key,
    event_id_for,
    inbound_event_id,
    saved_json,
)


def test_an_event_id_is_worked_out_from_the_session_and_the_seq() -> None:
    assert event_id_for("sess_a", 3) == (
        "7cd2f7a979b5ed4be72a147582134257af2fbb2b80e738b5070b9bf29a570c6d"
    )


def test_a_user_message_id_is_worked_out_from_the_channel_and_its_own_id() -> None:
    assert inbound_event_id("telegram", "8812") == (
        "4fe4741d8507af924e88a6278fb55d33a63e0003ac1c2a4fa91bc0e9d1e2070f"
    )


def test_text_outside_ascii_is_hashed_as_itself() -> None:
    assert inbound_event_id("terminal", "café-7") == (
        "80a80829719c66ce85ccb1e04b0d5e73ff50f9110363b5918854435ac6b00c0f"
    )


def test_an_effect_key_is_worked_out_from_the_call_and_its_attempt() -> None:
    assert effect_key("sess_a", 41, 0, 0) == (
        "9350ce5076ff77bdd17f0709a47c2cfb0b0ee6a3bc95859d764e694e23ea0aa3"
    )


def test_a_second_attempt_of_one_call_has_its_own_key() -> None:
    assert effect_key("sess_a", 41, 0, 1) != effect_key("sess_a", 41, 0, 0)


def test_two_kinds_of_id_from_the_same_values_never_meet() -> None:
    assert event_id_for("telegram", 8812) != inbound_event_id("telegram", "8812")


def test_the_action_hash_example_follows_from_its_formula() -> None:
    # The function arrives with its first caller. Its formula is fixed now, so
    # the example that every later caller relies on is pinned from day one.
    args = saved_json({"path": "notes.txt", "text": "tea"})
    listed = canonical_json(["action", "write_file", args, "file:notes.txt"])
    assert hashlib.sha256(listed.encode()).hexdigest() == (
        "92511298e90d8b74249f007ad92457d731c9b26ccec4e0d3c8d76ea98ce5491c"
    )
