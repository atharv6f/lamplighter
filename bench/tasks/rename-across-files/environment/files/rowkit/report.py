"""Prints a short report of a text's rows."""

from rowkit.source import fetch_rows


def report(text):
    """Give one line that says how many rows there are and the first."""
    rows = fetch_rows(text)
    first = rows[0] if rows else "none"
    return f"{len(rows)} rows, first: {first}"
