"""The repeat rule, on the calls of a run as the view gives them."""

from lamplighter.core.repeat_rule import deserves_warning, ends_the_run
from lamplighter.core.run_view import RunView, view_of
from tests.run_history import History, call, unparsed


def view(history: History) -> RunView:
    made = view_of(history.events)
    assert isinstance(made, RunView), made
    return made


def test_one_per_reply_the_third_is_warned_and_the_fourth_ends_the_run() -> None:
    history = History().start()
    for place in range(3):
        history.step_with(call())
        assert deserves_warning(view(history), place, "same") == (place == 2)
        history.ran(call(), "same")
    history.step_with(call())
    assert ends_the_run(view(history), 3)


def test_four_in_one_reply_all_run_and_the_next_reply_ends_the_run() -> None:
    history = History().start()
    calls = [call(f"call_{n}") for n in range(4)]
    history.step_with(*calls)
    for place, made in enumerate(calls):
        assert not ends_the_run(view(history), place)
        assert deserves_warning(view(history), place, "same") == (place >= 2)
        history.ran(made, "same")
    history.step_with(call("call_x"))
    assert ends_the_run(view(history), 4)


def test_the_same_call_with_a_new_result_each_time_is_never_stopped() -> None:
    history = History().start()
    for place in range(6):
        history.step_with(call(name="current_time"))
        assert not ends_the_run(view(history), place)
        assert not deserves_warning(view(history), place, f"12:0{place}")
        history.ran(call(name="current_time"), f"12:0{place}")


def test_a_different_call_in_between_starts_the_count_again() -> None:
    history = History().start()
    for made in (call(), call(), call(seconds=2), call()):
        history.step_with(made)
        history.ran(made, "same")
    history.step_with(call())
    assert not ends_the_run(view(history), 4)
    assert not deserves_warning(view(history), 4, "same")


def test_a_different_call_after_three_identical_ones_is_run() -> None:
    history = History().start()
    for _ in range(3):
        history.step_with(call())
        history.ran(call(), "same")
    history.step_with(call(seconds=5))
    assert not ends_the_run(view(history), 3)


def test_the_same_broken_call_is_stopped_the_same_way() -> None:
    history = History().start()
    for place in range(3):
        history.step_with(unparsed())
        assert deserves_warning(view(history), place, "bad") == (place == 2)
        history.result(unparsed(), "bad", harness="arguments_unparsed")
    history.step_with(unparsed())
    assert ends_the_run(view(history), 3)


def test_the_same_cut_off_call_is_stopped_the_same_way() -> None:
    history = History().start()
    for _ in range(3):
        history.step_with(call(), reason="max_output")
        history.result(call(), "cut", harness="cut_off")
    history.step_with(call(), reason="max_output")
    assert ends_the_run(view(history), 3)


def test_two_before_is_not_enough_to_end_the_run() -> None:
    history = History().start()
    for _ in range(2):
        history.step_with(call())
        history.ran(call(), "same")
    history.step_with(call())
    assert not ends_the_run(view(history), 2)
