"""Gives the first row."""

from rowkit import source


def first_row(text):
    """Give the first row, or None when there is none."""
    rows = source.fetch_rows(text)
    return rows[0] if rows else None
