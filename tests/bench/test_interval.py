"""The interval and the mean, in whole hundredths of a point."""

import math
import statistics

import pytest

from lamplighter.bench import T_THOUSANDTHS, half_width_bp, mean_bp


def test_the_worked_case_of_four_tasks_up_one_attempt_in_twenty() -> None:
    sixths = [2, 2, 2, 2] + [0] * 16
    assert mean_bp(sixths) == 667
    assert half_width_bp(sixths) == 528


def test_equal_differences_have_no_spread() -> None:
    assert half_width_bp([3] * 12) == 0


def test_one_task_has_no_interval() -> None:
    assert half_width_bp([4]) is None
    assert mean_bp([4]) == 6667


@pytest.mark.parametrize(("total", "mean"), [(3, 313), (-3, -313)])
def test_a_half_is_rounded_away_from_zero(total: int, mean: int) -> None:
    # 3 sixths over 16 tasks is 312.5 hundredths of a point.
    assert mean_bp([total] + [0] * 15) == mean


def test_the_half_width_is_t_times_the_standard_error_rounded_down() -> None:
    sixths = [6, -2, 3, 0, 1, -6, 4, 2, 2, -1, 5]
    n = len(sixths)
    error = statistics.stdev(sixths) / math.sqrt(n)
    expected = T_THOUSANDTHS[n - 2] / 1000 * error * 10000 / 6
    half = half_width_bp(sixths)
    assert half is not None
    assert half <= expected < half + 1


def test_the_table_is_students_t_for_one_to_twenty_nine_degrees() -> None:
    assert len(T_THOUSANDTHS) == 29
    assert (T_THOUSANDTHS[0], T_THOUSANDTHS[18], T_THOUSANDTHS[-1]) == (
        6314,
        1729,
        1699,
    )
