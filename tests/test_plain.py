import pytest
from model_samples import REQUEST, TEXT, TOOL_CALL, USAGE

from lamplighter.ports import (
    JsonValue,
    Message,
    ModelRequest,
    TextPart,
    ToolCallPart,
    Usage,
    from_plain,
    to_plain,
)


def _plain_object(record: object) -> dict[str, JsonValue]:
    plain = to_plain(record)
    assert isinstance(plain, dict)
    return plain


def test_lists_come_back_as_tuples() -> None:
    again = from_plain(ModelRequest, to_plain(REQUEST))
    assert isinstance(again.system, tuple)
    assert isinstance(again.messages[1].parts, tuple)


def test_a_part_is_rebuilt_as_the_record_its_kind_names() -> None:
    plain = to_plain(Message(role="assistant", parts=(TEXT, TOOL_CALL)))
    again = from_plain(Message, plain)
    assert [type(part) for part in again.parts] == [TextPart, ToolCallPart]


def test_an_unknown_kind_is_rejected() -> None:
    plain = _plain_object(Message(role="assistant", parts=(TEXT,)))
    plain["parts"] = [{"kind": "picture", "text": "x"}]
    with pytest.raises(ValueError, match="picture"):
        from_plain(Message, plain)


def test_a_value_outside_a_closed_list_is_rejected() -> None:
    plain = _plain_object(Message(role="user", parts=(TEXT,)))
    plain["role"] = "narrator"
    with pytest.raises(ValueError, match="narrator"):
        from_plain(Message, plain)


def test_a_missing_field_is_rejected() -> None:
    plain = _plain_object(USAGE)
    del plain["output"]
    with pytest.raises(ValueError, match="Usage"):
        from_plain(Usage, plain)


def test_an_extra_field_is_rejected() -> None:
    plain = _plain_object(USAGE)
    plain["cost"] = 3
    with pytest.raises(ValueError, match="Usage"):
        from_plain(Usage, plain)


@pytest.mark.parametrize("wrong", ["500", 5.0, True, [500]])
def test_a_value_of_the_wrong_type_is_rejected(wrong: JsonValue) -> None:
    plain = _plain_object(USAGE)
    plain["input_uncached"] = wrong
    with pytest.raises(ValueError, match="expected int"):
        from_plain(Usage, plain)


def test_an_empty_count_stays_empty_and_does_not_become_zero() -> None:
    usage = Usage(input_uncached=None, cache_read=0, cache_write=None, output=None)
    again = from_plain(Usage, to_plain(usage))
    assert again.input_uncached is None
    assert again.cache_read == 0
