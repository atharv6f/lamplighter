"""The count of sign patterns of per-task differences above, at and below a sum."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SignCounts:
    """How many of the `total` sign patterns sum above, at or below the seen sum."""

    above: int
    at: int
    below: int
    total: int


def sign_counts(differences: Sequence[int]) -> SignCounts:
    """Count every pattern of signs on the non-zero differences, without listing one.

    Under "the change does nothing" each non-zero difference was as likely
    to come out with the other sign, so all 2^k patterns are equally
    likely. With no non-zero difference there is one pattern, and it is the
    one seen.
    """
    sizes = [abs(d) for d in differences if d != 0]
    span = sum(sizes)
    # ways[s + span] is the number of patterns whose sum is s.
    ways = [0] * (2 * span + 1)
    ways[span] = 1
    for size in sizes:
        moved = [0] * (2 * span + 1)
        for place, count in enumerate(ways):
            if count:
                moved[place + size] += count
                moved[place - size] += count
        ways = moved
    seen = sum(differences) + span
    return SignCounts(
        above=sum(ways[seen + 1 :]),
        at=ways[seen],
        below=sum(ways[:seen]),
        total=2 ** len(sizes),
    )
