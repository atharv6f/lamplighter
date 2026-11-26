"""The rules for text that names something, shared by every store's input check."""

import re
from datetime import datetime

# A letter or digit first, so an id never reads as an option on a command
# line, and only characters that are safe in a file name.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CHANNEL = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HASH = re.compile(r"[0-9a-f]{64}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")

LONGEST_NATIVE_TEXT = 256


def is_id(text: str) -> bool:
    """Say whether the text is a valid session id or run id."""
    return _ID.fullmatch(text) is not None


def is_event_type(text: str) -> bool:
    """Say whether the text is a valid event type."""
    return _EVENT_TYPE.fullmatch(text) is not None


def is_channel(text: str) -> bool:
    """Say whether the text is a valid channel name."""
    return _CHANNEL.fullmatch(text) is not None


def is_native_text(text: str) -> bool:
    """Say whether the text is a valid id from a channel: 1 to 256 characters."""
    return 1 <= len(text) <= LONGEST_NATIVE_TEXT


def is_hash(text: str) -> bool:
    """Say whether the text is a SHA-256 written as 64 lowercase hex characters."""
    return _HASH.fullmatch(text) is not None


def is_time_text(text: str) -> bool:
    """Say whether the text is a time in the one saved form, and a real date."""
    if _TIME.fullmatch(text) is None:
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True
