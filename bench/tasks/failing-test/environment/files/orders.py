"""Adds up the lines of an order."""


def total_cents(lines: list[tuple[int, int]]) -> int:
    """Give the order's total: each line's price in cents times its quantity."""
    return sum(price for price, quantity in lines)
