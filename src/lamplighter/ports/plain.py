"""Turns port records into plain JSON values and back."""

import dataclasses
import types
from collections.abc import Callable, Mapping
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
    """Rebuild a record from its JSON form, which must hold exactly its fields.

    Raises ValueError when the data does not fit the record, because data that
    was written by `to_plain` always fits.
    """
    return cast(T, _read(record_type, data, _exact_record))


def from_stored[T](record_type: type[T], data: JsonValue) -> T:
    """Rebuild a record from data that was saved, maybe by older or newer code.

    A field the record does not have is ignored, because newer code may have
    added it. A field the record has and the data lacks takes the record's
    default, because older code did not know it. Raises ValueError when a
    field with no default is missing or a value has the wrong type.
    """
    return cast(T, _read(record_type, data, _stored_record))


type _RecordReader = Callable[[Any, Any], Any]


def _read(hint: Any, data: Any, record: _RecordReader) -> Any:
    if isinstance(hint, TypeAliasType):
        return _read(hint.__value__, data, record)
    origin = get_origin(hint)
    if origin is Literal:
        return _read_literal(hint, data)
    if origin in (Union, types.UnionType):
        return _read_union(hint, data, record)
    if origin is tuple:
        item_hint = get_args(hint)[0]
        return tuple(_read(item_hint, item, record) for item in _expect(list, data))
    if origin is Mapping:
        return _expect(dict, data)
    if dataclasses.is_dataclass(hint):
        return record(hint, data)
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


def _read_union(hint: Any, data: Any, record: _RecordReader) -> Any:
    members = [m for m in get_args(hint) if m is not type(None)]
    if data is None and len(members) < len(get_args(hint)):
        return None
    if len(members) == 1:
        return _read(members[0], data, record)
    kind = _expect(dict, data).get("kind")
    for member in members:
        if get_args(get_type_hints(member)["kind"]) == (kind,):
            return record(member, data)
    raise ValueError(f"no record has kind {kind!r}")


def _exact_record(record_type: Any, data: Any) -> Any:
    plain = _expect(dict, data)
    names = {f.name for f in dataclasses.fields(record_type)}
    if plain.keys() != names:
        raise ValueError(
            f"{record_type.__name__} needs exactly the fields {sorted(names)}"
        )
    hints = get_type_hints(record_type)
    return record_type(
        **{name: _read(hints[name], plain[name], _exact_record) for name in names}
    )


def _stored_record(record_type: Any, data: Any) -> Any:
    plain = _expect(dict, data)
    hints = get_type_hints(record_type)
    values: dict[str, Any] = {}
    for f in dataclasses.fields(record_type):
        if f.name in plain:
            values[f.name] = _read(hints[f.name], plain[f.name], _stored_record)
        elif not _has_default(f):
            raise ValueError(f"{record_type.__name__} is missing the field {f.name}")
    return record_type(**values)


def _has_default(f: dataclasses.Field[Any]) -> bool:
    return (
        f.default is not dataclasses.MISSING
        or f.default_factory is not dataclasses.MISSING
    )
