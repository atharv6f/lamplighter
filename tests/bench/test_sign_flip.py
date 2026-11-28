"""The count of sign patterns equals a listing of every pattern, on every small case."""

import itertools
import random
from fractions import Fraction

import pytest

from lamplighter.bench import SignCounts, sign_counts

# The reference's fixed cases, in its units of thirds of a task, with its
# exact p-values. The gate counts in sixths, so each unit is two of them.
FIXED = [
    ([1, 1, 1] + [0] * 17, Fraction(1, 16), Fraction(15, 16)),
    ([1, 1, 1, 1] + [0] * 16, Fraction(1, 32), Fraction(31, 32)),
    ([1] * 5 + [0] * 15, Fraction(1, 64), Fraction(63, 64)),
    ([3, 3] + [0] * 18, Fraction(1, 8), Fraction(7, 8)),
    ([3] * 4 + [0] * 16, Fraction(1, 32), Fraction(31, 32)),
    ([3] * 5 + [-1] + [0] * 14, Fraction(3, 128), Fraction(125, 128)),
    ([1] * 6 + [-1] * 2 + [0] * 12, Fraction(23, 256), Fraction(233, 256)),
    ([-3] * 14 + [0] * 6, Fraction(32767, 32768), Fraction(1, 32768)),
]


def _mid_p(counts: SignCounts) -> tuple[Fraction, Fraction]:
    patterns = 2 * counts.total
    return (
        Fraction(2 * counts.above + counts.at, patterns),
        Fraction(2 * counts.below + counts.at, patterns),
    )


def _listed(differences: list[int]) -> SignCounts:
    # The slow way: every pattern of signs on the non-zero differences.
    moved = [d for d in differences if d]
    seen = sum(differences)
    sums = [
        sum(sign * abs(d) for sign, d in zip(signs, moved, strict=True))
        for signs in itertools.product((1, -1), repeat=len(moved))
    ]
    return SignCounts(
        above=sum(s > seen for s in sums),
        at=sum(s == seen for s in sums),
        below=sum(s < seen for s in sums),
        total=len(sums),
    )


@pytest.mark.parametrize(("thirds", "better", "worse"), FIXED)
def test_the_references_fixed_cases_to_the_exact_fraction(
    thirds: list[int], better: Fraction, worse: Fraction
) -> None:
    assert _mid_p(sign_counts([2 * d for d in thirds])) == (better, worse)


def test_nothing_moved_is_one_half_each_way() -> None:
    counts = sign_counts([0] * 20)
    assert counts == SignCounts(above=0, at=1, below=0, total=1)
    assert _mid_p(counts) == (Fraction(1, 2), Fraction(1, 2))


def test_it_equals_every_pattern_listed_on_random_small_lists() -> None:
    draw = random.Random(20261128)
    for _ in range(3000):
        size = draw.randint(0, 8)
        differences = [draw.randint(-6, 6) for _ in range(size)]
        assert sign_counts(differences) == _listed(differences), differences


def test_the_largest_task_set_is_counted_without_listing() -> None:
    counts = sign_counts([6] * 30)
    assert counts == SignCounts(above=0, at=1, below=2**30 - 1, total=2**30)
