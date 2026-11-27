"""The examples here are pinned. A change to a formula would orphan saved rows."""

import hashlib
import re

from lamplighter.ports import (
    canonical_json,
    effect_key,
    event_id_for,
    inbound_event_id,
    out_id_for,
    saved_json,
    span_id_for,
    trace_id_for,
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


def test_an_outbound_id_is_worked_out_from_the_session_and_the_seq() -> None:
    assert out_id_for("demo-session", 12) == (
        "9131f9196d24c6b60400011912c7ea9b0d0c4d966a4b0c60ff33d9f42ed17e2a"
    )


def test_a_trace_id_is_worked_out_from_the_layout_the_session_and_the_run() -> None:
    assert trace_id_for(1, "sess_a", "run_a") == "0e9989490d36bd20896dd8df2ee81290"


def test_a_span_id_is_worked_out_from_the_layout_the_run_the_role_and_the_event() -> (
    None
):
    assert span_id_for(1, "run_a", "model", event_id_for("sess_a", 4)) == (
        "92f368cbf10f553c"
    )


def test_the_ids_are_lowercase_hex_of_the_lengths_the_wire_format_takes() -> None:
    trace = trace_id_for(1, "sess_a", "run_a")
    span = span_id_for(1, "run_a", "run", event_id_for("sess_a", 2))
    assert re.fullmatch("[0-9a-f]{32}", trace)
    assert re.fullmatch("[0-9a-f]{16}", span)


def test_a_step_and_its_model_call_are_made_from_one_event_and_differ() -> None:
    request = event_id_for("sess_a", 4)
    assert span_id_for(1, "run_a", "step", request) != span_id_for(
        1, "run_a", "model", request
    )


def test_a_new_layout_number_changes_every_id() -> None:
    event = event_id_for("sess_a", 4)
    assert trace_id_for(2, "sess_a", "run_a") != trace_id_for(1, "sess_a", "run_a")
    for role in ("run", "step", "model", "tool"):
        assert span_id_for(2, "run_a", role, event) != span_id_for(
            1, "run_a", role, event
        )


def test_two_runs_whose_events_share_their_ids_share_no_span_id() -> None:
    # A session deleted and started again under its id gives its events the
    # old ids. Its runs have new ids, so no span id comes back.
    for seq in range(1, 20):
        event = event_id_for("sess_a", seq)
        for role in ("run", "step", "model", "tool"):
            assert span_id_for(1, "run_new", role, event) != span_id_for(
                1, "run_old", role, event
            )


def test_two_sessions_with_one_run_id_have_two_traces() -> None:
    assert trace_id_for(1, "sess_a", "run_a") != trace_id_for(1, "sess_b", "run_a")


def test_the_two_span_formulas_are_the_first_hex_of_a_sha256_of_their_list() -> None:
    event = event_id_for("sess_a", 4)
    trace = canonical_json(["trace", 1, "sess_a", "run_a"])
    span = canonical_json(["span", 1, "run_a", "tool", event])
    assert (
        trace_id_for(1, "sess_a", "run_a")
        == (hashlib.sha256(trace.encode()).hexdigest()[:32])
    )
    assert (
        span_id_for(1, "run_a", "tool", event)
        == (hashlib.sha256(span.encode()).hexdigest()[:16])
    )
