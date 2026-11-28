"""Splits a list into pages."""


def pages(items: list, size: int) -> list[list]:
    """Split `items` into pages of `size` items, in order.

    The last page holds what is left, which may be fewer than `size`.
    A list with no items has no pages.
    """
    found = []
    start = 0
    while start + size < len(items):
        found.append(items[start : start + size])
        start += size
    found.append(items[start : len(items) - 1])
    return found
