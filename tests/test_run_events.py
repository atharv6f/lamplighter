"""How the loop measures the time a call took."""

import pytest

from lamplighter.core.run_events import elapsed_ms


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (0.0, 0.0, 0),
        (0.0, 0.0004, 1),
        (5.0, 5.0011, 2),
        (0.1, 0.3, 200),
        (0.0, 1.0, 1_000),
        # Less than a microsecond is float noise, and counts as nothing.
        (10.0, 10.000_000_4, 0),
    ],
)
def test_a_call_is_rounded_up_to_a_whole_millisecond(
    start: float, end: float, expected: int
) -> None:
    assert elapsed_ms(start, end) == expected


def test_a_float_a_hair_over_a_millisecond_is_not_counted_as_one_more() -> None:
    # 0.3 - 0.1 is 0.19999999999999998 in floating point, and 1.1 - 1.0 is
    # 0.10000000000000009, which a plain ceiling would make 101 ms.
    assert elapsed_ms(1.0, 1.1) == 100
