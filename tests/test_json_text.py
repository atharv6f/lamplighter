import math
from typing import cast

import pytest

from lamplighter.ports import (
    EffectResult,
    JsonObject,
    as_saved,
    canonical_json,
    read_saved_json,
    saved_json,
    saved_size,
)
from tests.model_samples import TOOL_RESULT


def test_canonical_json_sorts_keys_and_has_no_spaces() -> None:
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == (
        '{"a":[true,null,"x"],"b":1}'
    )


def test_text_outside_ascii_is_written_as_itself_and_not_as_an_escape() -> None:
    assert canonical_json(["café"]) == '["café"]'
    assert saved_json({"text": "café"}) == '{"text":"café"}'


def test_canonical_json_refuses_a_float_anywhere() -> None:
    with pytest.raises(ValueError, match="float"):
        canonical_json({"a": [1, {"b": 0.5}]})


def test_canonical_json_refuses_a_key_outside_ascii() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        canonical_json({"clé": 1})


def test_saved_json_keeps_a_float() -> None:
    assert saved_json({"ratio": 0.5}) == '{"ratio":0.5}'


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_saved_json_refuses_a_number_json_cannot_hold(number: float) -> None:
    with pytest.raises(ValueError):
        saved_json({"x": number})


def test_saved_json_refuses_text_that_is_not_utf8() -> None:
    with pytest.raises(ValueError):
        saved_json({"text": "\ud800"})


def test_saved_json_refuses_a_value_that_is_not_json() -> None:
    with pytest.raises(ValueError):
        saved_json({"when": object()})


def test_a_record_is_saved_as_its_plain_form() -> None:
    assert read_saved_json(saved_json(TOOL_RESULT)) == {
        "call_id": "call_1",
        "is_error": False,
        "kind": "tool_result",
        "text": "milk\neggs\ntea",
    }


def test_the_saved_size_counts_bytes_and_not_characters() -> None:
    assert saved_size({"t": "é"}) == len('{"t":"é"}'.encode()) == 10


def test_text_that_is_not_json_is_refused_on_reading() -> None:
    with pytest.raises(ValueError):
        read_saved_json("{not json")


def test_as_saved_gives_a_record_as_a_reader_of_its_saved_form_gets_it() -> None:
    result = EffectResult(
        outcome="done", output=cast(JsonObject, {"lines": ("milk", "tea")})
    )
    assert as_saved(EffectResult, result) == EffectResult(
        outcome="done", output={"lines": ["milk", "tea"]}
    )
