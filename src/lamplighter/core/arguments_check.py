"""Checks a tool call's arguments against the tool's schema, before anything runs.

Only nine keywords of JSON Schema are known, which is all the tools here
use. A schema with any other keyword is refused when the process starts,
so the check never passes a rule it did not read. Nothing is converted: a
text where a number belongs is a problem, and the sentence says so.
"""

import json
from collections.abc import Mapping, Sequence
from typing import cast

from lamplighter.ports import JsonObject, JsonValue

_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
_KNOWN = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "description",
        "title",
    }
)
_TOP = "the arguments"


def problems(schema: JsonObject, arguments: JsonObject) -> tuple[str, ...]:
    """Give one short sentence for each way the arguments break the schema.

    An empty answer means they fit. The schema must be one that
    `unsupported` finds nothing in.
    """
    found: list[str] = []
    _check(schema, arguments, _TOP, found)
    return tuple(found)


def unsupported(schema: JsonObject) -> tuple[str, ...]:
    """Name each part of a schema that the check does not know.

    The top level must be an object schema. An empty answer means the check
    understands the whole schema.
    """
    found: list[str] = []
    if schema.get("type") != "object":
        found.append("the top level is not an object schema")
    _known_parts(schema, "", found)
    return tuple(found)


def _known_parts(schema: JsonObject, where: str, found: list[str]) -> None:
    for keyword, value in schema.items():
        named = f"{where}{keyword}"
        if keyword not in _KNOWN:
            found.append(named)
        elif not _well_formed(keyword, value):
            found.append(f"{named} as written")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, inner in properties.items():
            if isinstance(inner, dict):
                _known_parts(inner, f"{where}{name}.", found)
            else:
                found.append(f"{where}properties.{name} as written")


def _well_formed(keyword: str, value: JsonValue) -> bool:  # noqa: C901
    # One branch per keyword keeps each shape next to its name.
    if keyword == "type":
        return isinstance(value, str) and value in _TYPES
    if keyword == "properties":
        return isinstance(value, dict)
    if keyword == "required":
        return isinstance(value, list) and all(isinstance(n, str) for n in value)
    if keyword == "additionalProperties":
        return isinstance(value, bool)
    if keyword == "enum":
        return isinstance(value, list) and bool(value)
    if keyword in ("minimum", "maximum"):
        return _is_number(value)
    if keyword in ("minLength", "maxLength"):
        return type(value) is int and value >= 0
    return isinstance(value, str)


def _check(schema: JsonObject, value: JsonValue, where: str, found: list[str]) -> None:
    wanted = schema.get("type")
    if isinstance(wanted, str) and not _is_type(value, wanted):
        found.append(f"{where}: must be {_named(wanted)}, not {_kind_of(value)}")
        return
    _check_enum(schema, value, where, found)
    _check_bounds(schema, value, where, found)
    _check_lengths(schema, value, where, found)
    if isinstance(value, dict):
        _check_object(schema, value, where, found)


def _check_enum(
    schema: JsonObject, value: JsonValue, where: str, found: list[str]
) -> None:
    allowed = schema.get("enum")
    if not isinstance(allowed, list):
        return
    if not any(_same(value, option) for option in allowed):
        listed = ", ".join(json.dumps(option, ensure_ascii=False) for option in allowed)
        found.append(f"{where}: must be one of {listed}")


def _check_bounds(
    schema: JsonObject, value: JsonValue, where: str, found: list[str]
) -> None:
    if not _is_number(value):
        return
    number = cast(int | float, value)
    low, high = schema.get("minimum"), schema.get("maximum")
    if _is_number(low) and number < cast(int | float, low):
        found.append(f"{where}: must be at least {low}")
    if _is_number(high) and number > cast(int | float, high):
        found.append(f"{where}: must be at most {high}")


def _check_lengths(
    schema: JsonObject, value: JsonValue, where: str, found: list[str]
) -> None:
    if not isinstance(value, str):
        return
    shortest, longest = schema.get("minLength"), schema.get("maxLength")
    if type(shortest) is int and len(value) < shortest:
        found.append(f"{where}: must have at least {shortest} characters")
    if type(longest) is int and len(value) > longest:
        found.append(f"{where}: must have at most {longest} characters")


def _check_object(
    schema: JsonObject, value: Mapping[str, JsonValue], where: str, found: list[str]
) -> None:
    properties = schema.get("properties")
    listed = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    for name in required if isinstance(required, list) else []:
        if isinstance(name, str) and name not in value:
            found.append(f"{_inside(where, name)}: is required")
    closed = schema.get("additionalProperties") is False
    for name, given in value.items():
        inner = listed.get(name)
        if isinstance(inner, dict):
            _check(inner, given, _inside(where, name), found)
        elif closed:
            found.append(f"{_inside(where, name)}: is not an argument of this tool")


def _inside(where: str, name: str) -> str:
    return name if where == _TOP else f"{where}.{name}"


def _is_type(value: JsonValue, wanted: str) -> bool:
    # bool is a kind of int in Python, and JSON keeps the two apart.
    match wanted:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return type(value) is int
        case "number":
            return _is_number(value)
        case "boolean":
            return isinstance(value, bool)
        case _:
            return value is None


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _same(value: JsonValue, option: JsonValue) -> bool:
    # True == 1 in Python, so the types must match as well as the values.
    return _kind_of(value) == _kind_of(option) and value == option


_ARTICLES = {
    "object": "an object",
    "array": "an array",
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "a boolean",
    "null": "null",
}


def _named(wanted: str) -> str:
    return _ARTICLES[wanted]


def _kind_of(value: JsonValue) -> str:
    if isinstance(value, bool):
        return _ARTICLES["boolean"]
    if type(value) is int:
        return _ARTICLES["integer"]
    if isinstance(value, float):
        return _ARTICLES["number"]
    if isinstance(value, str):
        return _ARTICLES["string"]
    if isinstance(value, dict):
        return _ARTICLES["object"]
    if isinstance(value, Sequence):
        return _ARTICLES["array"]
    return _ARTICLES["null"]
