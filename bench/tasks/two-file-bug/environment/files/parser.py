"""Reads a settings file of `key=value` lines."""


def parse(text: str) -> list[tuple[str, str]]:
    """Give each setting as a key and a value, in the order of the file.

    Blank lines and lines that start with `#` are passed over. The value
    is everything after the first `=`, and may itself hold `=`.
    """
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=")
        found.append((key.strip(), value.strip()))
    return found
