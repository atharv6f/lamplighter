import dataclasses

import pytest

from lamplighter.core import Prices, cost_micro_dollars
from lamplighter.ports import Usage

PRICES = Prices(
    input_uncached=4_000_000,
    cache_read=200_000,
    cache_write=5_000_000,
    output=20_000_000,
)
USAGE = Usage(input_uncached=500, cache_read=38_000, cache_write=1_500, output=800)


def test_a_cost_is_each_count_times_its_own_price() -> None:
    # 2,000 + 7,600 + 7,500 + 16,000
    assert cost_micro_dollars(USAGE, PRICES) == 33_100


@pytest.mark.parametrize(
    ("field", "cost"),
    [
        ("input_uncached", 4),
        ("cache_read", 2),
        ("cache_write", 5),
        ("output", 20),
    ],
)
def test_each_count_is_charged_at_its_own_price(field: str, cost: int) -> None:
    nothing = Usage(input_uncached=0, cache_read=0, cache_write=0, output=0)
    # Ten tokens for the cache read, so its price is not lost to rounding.
    count = 10 if field == "cache_read" else 1
    assert cost_micro_dollars(nothing, PRICES) == 0
    usage = dataclasses.replace(nothing, **{field: count})
    assert cost_micro_dollars(usage, PRICES) == cost


@pytest.mark.parametrize(
    "field", ["input_uncached", "cache_read", "cache_write", "output"]
)
def test_a_missing_count_gives_no_cost_and_not_a_lower_one(field: str) -> None:
    usage = dataclasses.replace(USAGE, **{field: None})
    assert cost_micro_dollars(usage, PRICES) is None


@pytest.mark.parametrize(
    ("tokens", "cost"),
    [(1, 0), (2, 0), (3, 1), (7, 1), (8, 2), (12, 2), (13, 3)],
)
def test_a_cost_is_rounded_to_the_nearest_micro_dollar(tokens: int, cost: int) -> None:
    # A cache read costs a fifth of a micro-dollar for one token.
    usage = Usage(input_uncached=0, cache_read=tokens, cache_write=0, output=0)
    assert cost_micro_dollars(usage, PRICES) == cost


def test_exactly_half_a_micro_dollar_rounds_up() -> None:
    half_each = Prices(input_uncached=500_000, cache_read=0, cache_write=0, output=0)
    one = Usage(input_uncached=1, cache_read=0, cache_write=0, output=0)
    assert cost_micro_dollars(one, half_each) == 1
    assert (
        cost_micro_dollars(dataclasses.replace(one, input_uncached=3), half_each) == 2
    )


def test_a_very_large_cost_stays_exact() -> None:
    # Too large for a floating point number to hold to the last digit.
    count = 10**17 + 1
    huge = Usage(input_uncached=count, cache_read=0, cache_write=0, output=count)
    assert cost_micro_dollars(huge, PRICES) == 24 * count


def test_a_price_table_cannot_be_changed() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        PRICES.output = 0  # pyright: ignore[reportAttributeAccessIssue]
