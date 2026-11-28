"""Counts rows."""

from rowkit.source import fetch_rows


def count_rows(text):
    """Give how many rows the text has."""
    return len(fetch_rows(text))
