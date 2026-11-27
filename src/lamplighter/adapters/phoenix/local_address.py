"""The one form of Phoenix's address that is sure to stay on this Mac. Pure."""

import re

# The exact text and nothing else: no parser reads it, so no spelling can
# read one way here and another way in the HTTP library.
_LOOPBACK = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?")
_LARGEST_PORT = 65_535


def local_address(setting: str) -> str | None:
    """Give the address to send to when `setting` stays on this Mac, or None.

    It stays only as `http://127.0.0.1:`, a port from 1 to 65535 in plain
    digits with no leading zero, and at most one `/` after it. The address
    given back is the setting without that `/`.
    """
    found = _LOOPBACK.fullmatch(setting)
    if found is None or int(found.group(1)) > _LARGEST_PORT:
        return None
    return setting.removesuffix("/")
