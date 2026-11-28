"""The two signals read from a run's events, and the four read from how it ends."""

import pytest

from lamplighter.core.harness_texts import with_repeat_warning
from lamplighter.core.route_signals import ending_signal, standing_signal
from lamplighter.ports import Event, TextPart
from tests.run_history import History, call

WRONG_KINDS = ("arguments_unparsed", "arguments_invalid", "unknown_tool")


def wrong(history: History, kind: str = "unknown_tool") -> Event:
    made = call(f"call_{len(history.events)}")
    return history.result(made, f"Made-up answer to a {kind} call.", harness=kind)


def good(history: History) -> Event:
    made = call(f"call_{len(history.events)}")
    history.intent(made)
    return history.result(made)


def warned(history: History) -> Event:
    made = call(f"call_{len(history.events)}")
    history.intent(made)
    return history.result(made, with_repeat_warning("Waited 1 seconds."))


def test_nothing_stands_in_a_run_with_no_events_after_its_choice() -> None:
    assert standing_signal(()) is None


@pytest.mark.parametrize("first", WRONG_KINDS)
@pytest.mark.parametrize("second", WRONG_KINDS)
def test_two_wrong_calls_in_a_row_of_any_two_kinds_stand_as_invalid_calls(
    first: str, second: str
) -> None:
    history = History()
    wrong(history, first)
    wrong(history, second)
    assert standing_signal(history.events) == "invalid_calls"


def test_one_wrong_call_is_not_a_signal() -> None:
    history = History()
    good(history)
    wrong(history)
    good(history)
    assert standing_signal(history.events) is None


def test_a_good_result_between_two_wrong_calls_breaks_the_streak() -> None:
    history = History()
    wrong(history)
    good(history)
    wrong(history)
    assert standing_signal(history.events) is None


def test_events_that_are_not_tool_results_do_not_break_the_streak() -> None:
    history = History()
    wrong(history)
    history.step_with(TextPart(text="Trying again."))
    history.intent(call("call_between"))
    wrong(history)
    assert standing_signal(history.events) == "invalid_calls"


def test_invalid_calls_stands_after_a_good_result_follows() -> None:
    history = History()
    wrong(history)
    wrong(history)
    good(history)
    good(history)
    assert standing_signal(history.events) == "invalid_calls"


@pytest.mark.parametrize(
    "kind",
    ["cut_off", "interrupted", "stopped", "out_of_time", "not_run", "unconfirmed"],
)
def test_a_result_the_harness_wrote_for_another_reason_is_not_a_wrong_call(
    kind: str,
) -> None:
    history = History()
    wrong(history)
    history.result(call("call_other"), "Made-up harness answer.", harness=kind)
    assert standing_signal(history.events) is None


def test_a_result_with_the_repeat_warning_stands_as_repeated_call() -> None:
    history = History()
    good(history)
    warned(history)
    good(history)
    assert standing_signal(history.events) == "repeated_call"


def test_the_warning_counts_only_at_the_end_of_the_text() -> None:
    history = History()
    text = with_repeat_warning("Waited 1 seconds.") + " And more."
    history.result(call("call_inside"), text)
    assert standing_signal(history.events) is None


def test_a_result_with_no_text_reads_as_no_warning() -> None:
    history = History()
    history.add("tool_result", {"call_id": "call_none", "is_error": False})
    assert standing_signal(history.events) is None


def test_when_both_stand_the_newer_is_the_signal() -> None:
    warned_first = History()
    warned(warned_first)
    wrong(warned_first)
    wrong(warned_first)
    assert standing_signal(warned_first.events) == "invalid_calls"

    wrong_first = History()
    wrong(wrong_first)
    wrong(wrong_first)
    warned(wrong_first)
    assert standing_signal(wrong_first.events) == "repeated_call"


def test_the_place_of_a_signal_is_where_it_first_stood() -> None:
    # The streak first reached two before the warning, so the warning is
    # newer even though the streak goes on after it.
    history = History()
    wrong(history)
    wrong(history)
    warned(history)
    wrong(history)
    wrong(history)
    assert standing_signal(history.events) == "repeated_call"


@pytest.mark.parametrize(
    ("detail", "signal"),
    [
        ("empty_reply", "empty_reply"),
        ("refused", "refused"),
        ("model_too_large", "too_large"),
        ("model_transient", "model_failed"),
        ("model_throttled", "model_failed"),
        ("model_unavailable", "model_failed"),
        ("model_timeout", "model_failed"),
        ("model_invalid_input", "model_failed"),
        ("model_denied", "model_failed"),
        ("model_fatal", "model_failed"),
    ],
)
def test_an_ending_of_the_model_gives_its_signal(detail: str, signal: str) -> None:
    assert ending_signal(detail) == signal


@pytest.mark.parametrize(
    "detail",
    ["", "repeated_call", "config_changed", "no_model", "steps", "seconds"],
)
def test_an_ending_that_is_not_the_model_s_gives_no_signal(detail: str) -> None:
    assert ending_signal(detail) is None
