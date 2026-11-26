"""Works out what one model call cost."""

from dataclasses import dataclass

from lamplighter.ports import Usage

_TOKENS_PER_PRICE = 1_000_000


@dataclass(frozen=True, slots=True)
class Prices:
    """What one million tokens of each kind cost, in whole micro-dollars.

    A micro-dollar is a millionth of a dollar. Whole numbers keep every sum
    exact, which sums of small fractions in floating point are not.
    """

    input_uncached: int
    cache_read: int
    cache_write: int
    output: int


def cost_micro_dollars(usage: Usage, prices: Prices) -> int | None:
    """Give the cost of one call in whole micro-dollars.

    The answer is None when any count is missing. A missing count is not
    zero, so a cost that left it out would be too low and look exact.
    """
    priced = (
        (usage.input_uncached, prices.input_uncached),
        (usage.cache_read, prices.cache_read),
        (usage.cache_write, prices.cache_write),
        (usage.output, prices.output),
    )
    total = 0
    for count, price in priced:
        if count is None:
            return None
        total += count * price
    # Rounded to the nearest micro-dollar, with a half going up.
    return (total + _TOKENS_PER_PRICE // 2) // _TOKENS_PER_PRICE
