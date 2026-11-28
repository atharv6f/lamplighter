"""Reads the rows of a text, one per line."""


def fetch_rows(text):
    """Give the rows of the text that are not empty, stripped."""
    return [line.strip() for line in text.splitlines() if line.strip()]
