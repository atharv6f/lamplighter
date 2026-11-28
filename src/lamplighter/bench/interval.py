"""A 90% interval around the mean per-task difference, in whole numbers."""

from collections.abc import Sequence
from math import isqrt

# The one-sided 95% value of Student's t in thousandths, for 1 to 29 degrees
# of freedom. The gate takes at most 30 tasks; a larger task set extends
# this table in the same change.
T_THOUSANDTHS = (
    6314, 2920, 2353, 2132, 2015, 1943, 1895, 1860, 1833, 1812,
    1796, 1782, 1771, 1761, 1753, 1746, 1740, 1734, 1729, 1725,
    1721, 1717, 1714, 1711, 1708, 1706, 1703, 1701, 1699,
)  # fmt: skip


def half_width_bp(sixths: Sequence[int]) -> int | None:
    """Give t times the standard error of the differences, in hundredths of a point.

    The differences are in sixths of a task. The value is rounded down. One
    difference has no spread, so it has no interval and gives None.
    """
    n = len(sixths)
    if n < 2:
        return None
    total = sum(sixths)
    squares = sum(d * d for d in sixths)
    t = T_THOUSANDTHS[n - 2]
    # Sixths to hundredths of a point is 10000 / 6, and t is in thousandths:
    # (t / 1000)^2 * (10000 / 6)^2 is 25 t^2 / 9.
    spread = 25 * t * t * (n * squares - total * total)
    return isqrt(spread // (9 * n * n * (n - 1)))


def mean_bp(sixths: Sequence[int]) -> int:
    """Give the mean of values in sixths, in hundredths of a point.

    It is rounded to the nearest, a half going away from zero. There is at
    least one value.
    """
    numerator = sum(sixths) * 10000
    denominator = 6 * len(sixths)
    rounded = (2 * abs(numerator) + denominator) // (2 * denominator)
    return rounded if numerator >= 0 else -rounded
