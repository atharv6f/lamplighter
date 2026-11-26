"""The check of a call's arguments against its tool's schema, sentence by sentence."""

import pytest

from lamplighter.core.arguments_check import problems, unsupported
from lamplighter.ports import JsonObject

SHELL: JsonObject = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "minLength": 1, "maxLength": 10},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
        "target": {"type": "string", "enum": ["content", "names"]},
        "ratio": {"type": "number", "minimum": 0.5},
        "flag": {"type": "boolean"},
        "nothing": {"type": "null"},
        "inner": {
            "type": "object",
            "properties": {"depth": {"type": "integer"}},
            "required": ["depth"],
            "additionalProperties": False,
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("arguments", "found"),
    [
        ({"command": "ls"}, ()),
        ({"command": "ls", "timeout_s": 600, "ratio": 0.5}, ()),
        ({"command": "ls", "ratio": 2}, ()),
        ({"command": "ls", "target": "names", "flag": False}, ()),
        ({"command": "ls", "nothing": None}, ()),
        ({"command": "ls", "inner": {"depth": 3}}, ()),
        ({}, ("command: is required",)),
        ({"command": 5}, ("command: must be a string, not an integer",)),
        ({"command": ""}, ("command: must have at least 1 characters",)),
        ({"command": "x" * 11}, ("command: must have at most 10 characters",)),
        (
            {"command": "ls", "timeout_s": "5"},
            ("timeout_s: must be an integer, not a string",),
        ),
        (
            {"command": "ls", "timeout_s": True},
            ("timeout_s: must be an integer, not a boolean",),
        ),
        (
            {"command": "ls", "timeout_s": 5.0},
            ("timeout_s: must be an integer, not a number",),
        ),
        ({"command": "ls", "timeout_s": 0}, ("timeout_s: must be at least 1",)),
        ({"command": "ls", "timeout_s": 601}, ("timeout_s: must be at most 600",)),
        ({"command": "ls", "ratio": 0.25}, ("ratio: must be at least 0.5",)),
        ({"command": "ls", "ratio": True}, ("ratio: must be a number, not a boolean",)),
        (
            {"command": "ls", "target": "lines"},
            ('target: must be one of "content", "names"',),
        ),
        ({"command": "ls", "flag": 1}, ("flag: must be a boolean, not an integer",)),
        ({"command": "ls", "nothing": 0}, ("nothing: must be null, not an integer",)),
        ({"command": "ls", "extra": 1}, ("extra: is not an argument of this tool",)),
        ({"command": "ls", "inner": {}}, ("inner.depth: is required",)),
        (
            {"command": "ls", "inner": {"depth": "deep", "wide": 2}},
            (
                "inner.depth: must be an integer, not a string",
                "inner.wide: is not an argument of this tool",
            ),
        ),
        (
            {"command": "ls", "inner": []},
            ("inner: must be an object, not an array",),
        ),
    ],
)
def test_each_keyword_passes_what_fits_and_names_what_does_not(
    arguments: JsonObject, found: tuple[str, ...]
) -> None:
    assert problems(SHELL, arguments) == found


def test_the_missing_names_come_first_then_each_given_argument_in_order() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["b", "a"],
        "additionalProperties": False,
    }
    assert problems(schema, {"z": 1, "y": 2}) == (
        "b: is required",
        "a: is required",
        "z: is not an argument of this tool",
        "y: is not an argument of this tool",
    )


def test_one_value_gives_its_bounds_and_its_length_in_that_order() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "word": {"type": "string", "enum": ["tea"], "maxLength": 2},
        },
    }
    assert problems(schema, {"word": "coffee"}) == (
        'word: must be one of "tea"',
        "word: must have at most 2 characters",
    )


def test_a_value_of_the_wrong_type_gets_only_the_type_sentence() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 5, "enum": [7]}},
    }
    assert problems(schema, {"n": "3"}) == ("n: must be an integer, not a string",)


def test_an_enum_keeps_true_apart_from_one() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {"n": {"enum": [1]}},
    }
    assert problems(schema, {"n": True}) == ("n: must be one of 1",)
    assert problems(schema, {"n": 1}) == ()


def test_an_argument_that_is_not_listed_passes_when_the_schema_allows_more() -> None:
    schema: JsonObject = {"type": "object", "properties": {}}
    assert problems(schema, {"anything": [1, 2]}) == ()


def test_a_whole_schema_of_the_nine_keywords_is_understood() -> None:
    assert unsupported(SHELL) == ()


@pytest.mark.parametrize(
    ("schema", "named"),
    [
        (
            {"type": "object", "properties": {"p": {"type": "string", "pattern": "a"}}},
            ("p.pattern",),
        ),
        ({"type": "object", "oneOf": []}, ("oneOf",)),
        (
            {"type": "object", "properties": {"l": {"type": "array", "items": {}}}},
            ("l.items",),
        ),
        (
            {"type": "object", "properties": {"t": {"type": ["string", "null"]}}},
            ("t.type as written",),
        ),
        (
            {"type": "object", "additionalProperties": {}},
            ("additionalProperties as written",),
        ),
        ({"properties": {}}, ("the top level is not an object schema",)),
        ({"type": "object", "properties": {"x": True}}, ("properties.x as written",)),
    ],
)
def test_a_keyword_the_check_does_not_know_is_named(
    schema: JsonObject, named: tuple[str, ...]
) -> None:
    assert unsupported(schema) == named


def test_a_description_and_a_title_are_ignored() -> None:
    schema: JsonObject = {
        "type": "object",
        "title": "Shell",
        "description": "Run one command.",
        "properties": {"c": {"type": "string", "description": "The command."}},
    }
    assert unsupported(schema) == ()
    assert problems(schema, {"c": "ls"}) == ()
