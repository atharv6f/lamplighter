import pytest

from lamplighter.adapters.anthropic.sse import SseEvent, SseReader

TWO_EVENTS = b'event: first\ndata: {"a": 1}\n\nevent: second\ndata: {"b": 2}\n\n'
EXPECTED = [SseEvent("first", '{"a": 1}'), SseEvent("second", '{"b": 2}')]


def read(body: bytes, piece_size: int) -> list[SseEvent]:
    reader = SseReader()
    events: list[SseEvent] = []
    for start in range(0, len(body), piece_size):
        events += reader.feed(body[start : start + piece_size])
    return events


@pytest.mark.parametrize("piece_size", [1, 2, 3, 7, 16, 1000])
def test_events_are_the_same_however_the_body_is_cut(piece_size: int) -> None:
    assert read(TWO_EVENTS, piece_size) == EXPECTED


@pytest.mark.parametrize("line_end", [b"\r\n", b"\r"])
@pytest.mark.parametrize("piece_size", [1, 5, 1000])
def test_other_line_endings_read_the_same(line_end: bytes, piece_size: int) -> None:
    assert read(TWO_EVENTS.replace(b"\n", line_end), piece_size) == EXPECTED


def test_an_event_is_given_only_when_its_blank_line_arrives() -> None:
    reader = SseReader()
    assert reader.feed(b'event: first\ndata: {"a": 1}\n') == []
    assert reader.feed(b"\n") == [EXPECTED[0]]


def test_an_event_cut_off_by_the_end_of_the_body_is_never_given() -> None:
    assert read(b'event: first\ndata: {"a": 1}\n\nevent: second\ndata: {"b"', 1000) == [
        EXPECTED[0]
    ]


def test_a_character_cut_in_half_by_the_network_is_put_back_together() -> None:
    body = "event: note\ndata: café ☕\n\n".encode()
    assert read(body, 1) == [SseEvent("note", "café ☕")]


def test_comment_lines_are_passed_over() -> None:
    assert read(b": keep-alive\nevent: first\n: another\ndata: x\n\n", 1000) == [
        SseEvent("first", "x")
    ]


def test_an_event_with_no_name_is_called_message() -> None:
    assert read(b"data: x\n\n", 1000) == [SseEvent("message", "x")]


def test_a_name_does_not_carry_over_to_the_next_event() -> None:
    assert read(b"event: first\ndata: x\n\ndata: y\n\n", 1000) == [
        SseEvent("first", "x"),
        SseEvent("message", "y"),
    ]


def test_a_block_with_no_data_is_not_an_event() -> None:
    assert read(b"event: first\n\ndata: y\n\n", 1000) == [SseEvent("message", "y")]


def test_several_data_lines_are_joined_with_a_newline() -> None:
    assert read(b"data: one\ndata: two\n\n", 1000) == [SseEvent("message", "one\ntwo")]


def test_only_one_space_after_the_colon_is_dropped() -> None:
    assert read(b"data:  two spaces\ndata:none\n\n", 1000) == [
        SseEvent("message", " two spaces\nnone")
    ]


def test_fields_the_reader_does_not_use_are_passed_over() -> None:
    assert read(b"id: 7\nretry: 100\nstrange\ndata: x\n\n", 1000) == [
        SseEvent("message", "x")
    ]
