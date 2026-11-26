"""Turns port records into plain JSON values and back."""

import dataclasses
import types
from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from lamplighter.ports.json_value import JsonValue


def to_plain(value: object) -> JsonValue:
    """Give the JSON form of a record or of any value inside one."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        items = cast(Mapping[str, object], value)
        return {key: to_plain(item) for key, item in items.items()}
    if isinstance(value, (tuple, list)):
        return [to_plain(item) for item in cast(list[object], value)]
    return cast(JsonValue, value)


def from_plain[T](record_type: type[T], data: JsonValue) -> T:
    """Rebuild a record from its JSON form.

    Raises ValueError when the data does not fit the record, because data that
    was written by `to_plain` always fits.
    """
    return cast(T, _read(record_type, data))


def _read(hint: Any, data: Any) -> Any:
    if isinstance(hint, TypeAliasType):
        return _read(hint.__value__, data)
    origin = get_origin(hint)
    if origin is Literal:
        return _read_literal(hint, data)
    if origin in (Union, types.UnionType):
        return _read_union(hint, data)
    if origin is tuple:
        return tuple(_read(get_args(hint)[0], item) for item in _expect(list, data))
    if origin is Mapping:
        return _expect(dict, data)
    if dataclasses.is_dataclass(hint):
        return _read_record(hint, data)
    return _expect(hint, data)


def _expect(plain_type: Any, data: Any) -> Any:
    # An exact type check, because bool is a subclass of int and must not pass as one.
    if type(data) is not plain_type:
        raise ValueError(f"expected {plain_type.__name__}, got {type(data).__name__}")
    return data


def _read_literal(hint: Any, data: Any) -> Any:
    if data not in get_args(hint):
        raise ValueError(f"{data!r} is not one of {get_args(hint)}")
    return data


def _read_union(hint: Any, data: Any) -> Any:
    members = [m for m in get_args(hint) if m is not type(None)]
    if data is None and len(members) < len(get_args(hint)):
        return None
    if len(members) == 1:
        return _read(members[0], data)
    kind = _expect(dict, data).get("kind")
    for member in members:
        if get_args(get_type_hints(member)["kind"]) == (kind,):
            return _read_record(member, data)
    raise ValueError(f"no record has kind {kind!r}")


def _read_record(record_type: Any, data: Any) -> Any:
    plain = _expect(dict, data)
    hints = get_type_hints(record_type)
    names = {f.name for f in dataclasses.fields(record_type)}
    if plain.keys() != names:
        raise ValueError(
            f"{record_type.__name__} needs exactly the fields {sorted(names)}"
        )
    return record_type(**{name: _read(hints[name], plain[name]) for name in names})
