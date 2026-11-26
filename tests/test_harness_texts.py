"""Every fixed sentence is pinned here against its literal text."""

import pytest

from lamplighter.core import harness_texts as texts


def test_the_texts_of_harness_results_are_pinned() -> None:
    assert texts.arguments_unparsed("wait", "not JSON: Expecting value") == (
        "The arguments for wait were not valid JSON: not JSON: Expecting value."
        " Send the call again with valid JSON."
    )
    assert texts.unknown_tool("fly", ["wait", "current_time"]) == (
        "There is no tool named fly. The tools are: current_time, wait."
    )
    assert texts.cut_off("wait") == (
        "The reply reached its output limit while wait was being written, so the"
        " call was not run. Send it again with shorter arguments, or do the work in"
        " smaller steps."
    )
    assert texts.interrupted("wait") == (
        "The harness was stopped while wait was running, and the call was not run"
        " again. Its outcome is unknown: it may have finished, partly run, or not"
        " run at all."
    )
    assert texts.stopped("wait") == (
        "wait was cut short because the user stopped the run. Its outcome is unknown."
    )
    assert texts.out_of_time("wait") == (
        "wait was cut short because the run reached its time limit."
        " Its outcome is unknown."
    )
    assert texts.not_run("wait") == "wait was not run, because the run ended first."
    assert texts.DID_NOT_STOP == "It did not stop when asked and may still be running."
    assert texts.tool_failed("wait", "timeout", "Too slow.") == (
        "wait failed (timeout): Too slow."
    )


def test_the_repeat_warning_is_pinned_and_comes_off_again() -> None:
    warned = texts.with_repeat_warning("12:00")
    assert warned == (
        "12:00\n\nNote from the harness: this call and its result are the same as"
        " the two before it. Making the same call again will end the run."
    )
    assert texts.without_repeat_warning(warned) == "12:00"
    assert texts.without_repeat_warning("12:00") == "12:00"


def test_the_notes_to_the_model_are_pinned() -> None:
    assert texts.EMPTY_REPLY_NOTE == (
        "Note from the harness, not from the user: your last reply was empty."
        " Carry on with the task, or say plainly that you are done."
    )
    assert texts.run_end_note("cancelled") == (
        "Note from the harness, not from the user: the user stopped the run above"
        " before it finished."
    )
    assert texts.run_end_note("limit") == (
        "Note from the harness, not from the user: the run above was ended by a"
        " limit before it finished."
    )
    assert texts.run_end_note("failed") == (
        "Note from the harness, not from the user: the run above ended on an error"
        " before it finished."
    )
    assert texts.run_end_note("done") is None


@pytest.mark.parametrize(
    ("limit", "allowed", "expected"),
    [
        ("steps", 40, "The run stopped at its limit of 40 steps."),
        ("seconds", 1200, "The run stopped at its limit of 1200 seconds."),
        ("tokens", 1000000, "The run stopped at its limit of 1000000 tokens."),
        ("micro_dollars", 1_000_000, "The run stopped at its limit of $1.00."),
        ("micro_dollars", 250_000, "The run stopped at its limit of $0.25."),
        ("micro_dollars", 1_234_999, "The run stopped at its limit of $1.23."),
        ("micro_dollars", 1_235_000, "The run stopped at its limit of $1.24."),
    ],
)
def test_the_limit_notices_are_pinned_with_their_numbers(
    limit: texts.LimitName, allowed: int, expected: str
) -> None:
    assert texts.limit_notice(limit, allowed) == (
        expected + " The work so far is saved. Send a message to go on."
    )


def test_the_other_notices_are_pinned() -> None:
    assert texts.STOPPED_NOTICE == "Stopped."
    assert texts.model_failed_notice("timeout", "The call took too long.") == (
        "The run ended because the model call failed: The call took too long."
        " Send a message to try again."
    )
    for kind in ("transient", "throttled", "unavailable"):
        assert texts.model_failed_notice(kind, "x.").endswith(
            " Send a message to try again."
        )
    assert texts.model_failed_notice("denied", "The key was refused.") == (
        "The run ended because the model call failed: The key was refused."
    )
    assert texts.EMPTY_REPLY_NOTICE == (
        "The run ended because the model sent an empty reply, and asking again did"
        " not help."
    )
    assert texts.REFUSED_NOTICE == "The model declined to answer that."
    assert texts.repeated_call_notice("wait") == (
        "The run ended because the model kept making the same call to wait and"
        " getting the same result."
    )
    assert texts.CONFIG_CHANGED_NOTICE == (
        "The settings changed while this run was open, so it was ended."
        " Send your message again."
    )
    assert texts.CUT_REPLY_LINE == "[The reply was cut off before the model finished.]"
