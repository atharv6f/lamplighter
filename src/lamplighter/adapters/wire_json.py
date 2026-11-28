"""Reads values out of parsed wire JSON and says so when one is not as documented."""

import json
from typing import Any, cast


class Unreadable(ValueError):
    """Wire data that does not have the shape the API documents."""


def parse_object(text: str) -> dict[str, Any]:
    """Parse JSON text that must hold an object."""
    try:
        return as_object(json.loads(text))
    except json.JSONDecodeError as problem:
        raise Unreadable(f"not JSON: {problem.msg}") from problem


def as_object(value: object) -> dict[str, Any]:
    """Give the value as an object, or say that it is not one."""
    if not isinstance(value, dict):
        raise Unreadable(f"expected an object, got {type(value).__name__}")
    return cast(dict[str, Any], value)


def text_in(holder: dict[str, Any], key: str) -> str:
    """Give the text under `key`."""
    value = holder.get(key)
    if not isinstance(value, str):
        raise Unreadable(f"'{key}' is not text")
    return value


def whole_in(holder: dict[str, Any], key: str) -> int:
    """Give the whole number under `key`."""
    value = holder.get(key)
    if type(value) is not int:
        raise Unreadable(f"'{key}' is not a whole number")
    return value


def object_or_empty(value: object) -> dict[str, Any]:
    """Give the value as an object, and an empty object when it is absent."""
    return {} if value is None else as_object(value)


def whole_or_none(holder: dict[str, Any], key: str) -> int | None:
    """Give the whole number under `key`, or None when there is none."""
    value = holder.get(key)
    return value if type(value) is int else None


def list_or_empty(value: object) -> list[Any]:
    """Give the value as a list, and an empty list when it is absent."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise Unreadable(f"expected a list, got {type(value).__name__}")
    return cast(list[Any], value)
