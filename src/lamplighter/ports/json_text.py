"""The two ways a value is written as JSON text: one to hash and one to save."""

import json

from lamplighter.ports.json_value import JsonValue
from lamplighter.ports.plain import to_plain


def canonical_json(value: object) -> str:
    """Give the one text of a value that is hashed.

    Keys are sorted, there are no spare spaces, and text is written as itself
    rather than as escapes, because the two ways of writing a letter such as
    é give different bytes and so different hashes. Raises ValueError for a
    float or a key outside ASCII, because either would make the text depend on
    how it was produced. A value that breaks these rules is a bug in the caller.
    """
    plain = to_plain(value)
    _refuse_floats_and_odd_keys(plain)
    return _dump(plain)


def saved_json(value: object) -> str:
    """Give the text a record or value is saved as.

    It follows the rules of the canonical form, except that floats are allowed,
    because a model's tool arguments can hold them and a reply is saved as it
    came. Raises ValueError for NaN, an infinity, or text that cannot be
    written as UTF-8, such as a lone surrogate.
    """
    text = _dump(to_plain(value))
    # A lone surrogate survives json.dumps and only fails here.
    text.encode("utf-8")
    return text


def saved_size(value: object) -> int:
    """Give the number of bytes a value takes when saved. Raises as saved_json."""
    return len(saved_json(value).encode("utf-8"))


def read_saved_json(text: str) -> JsonValue:
    """Read saved JSON text back into plain values. Raises ValueError if not JSON."""
    return json.loads(text)


def _dump(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except TypeError as error:
        raise ValueError(str(error)) from error


def _refuse_floats_and_odd_keys(value: JsonValue) -> None:
    if isinstance(value, float):
        raise ValueError("canonical JSON has no floats")
    if isinstance(value, dict):
        for key, item in value.items():
            if not key.isascii():
                raise ValueError(f"canonical JSON keys are ASCII, not {key!r}")
            _refuse_floats_and_odd_keys(item)
    if isinstance(value, list):
        for item in value:
            _refuse_floats_and_odd_keys(item)
