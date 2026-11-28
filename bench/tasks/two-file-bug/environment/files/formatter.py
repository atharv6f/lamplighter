"""Writes settings as a table."""


def table(settings: list[tuple[str, str]]) -> str:
    """Give one line per setting: the key, padded with spaces to the
    length of the longest key, then ` : ` and the value.
    """
    return "".join(f"{key[:8]:<8} : {value}\n" for key, value in settings)
