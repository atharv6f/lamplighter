"""The promises of the trace cursor port, checked on every cursor."""

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FileTraceCursor
from lamplighter.adapters.in_memory import InMemoryTraceCursor
from lamplighter.ports import CursorMark, Failure, TraceCursor, canonical_json

# Gives a new handle on one cursor each time it is called, as a second
# process on the same store would have.
type Place = Callable[[], TraceCursor]


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def in_memory() -> Place:
    cursor = InMemoryTraceCursor()
    return lambda: cursor


def in_a_file(folder: Path) -> Place:
    target = canonical_json(["http://127.0.0.1:6006", "lamplighter", 1])
    return lambda: FileTraceCursor(folder, target)


PLACES: dict[str, Callable[[Path], Place]] = {
    "in_memory": lambda _: in_memory(),
    "file": in_a_file,
}


@pytest.fixture(params=list(PLACES.values()), ids=list(PLACES))
def place(request: pytest.FixtureRequest, tmp_path: Path) -> Place:
    return request.param(tmp_path)


def mark(cursor: TraceCursor) -> CursorMark:
    read = run(cursor.read())
    assert isinstance(read, CursorMark), read
    return read


def moved(cursor: TraceCursor, seen: CursorMark, to: int) -> bool:
    result = run(cursor.move(seen, to))
    assert not isinstance(result, Failure), result
    return result


def reset(cursor: TraceCursor) -> None:
    result = run(cursor.reset())
    assert result is None, result


def test_a_new_cursor_is_at_position_0_with_no_reset(place: Place) -> None:
    assert mark(place()) == CursorMark(pos=0, resets=0)


def test_a_move_from_the_mark_that_was_read_goes_through(place: Place) -> None:
    cursor = place()

    assert moved(cursor, mark(cursor), 40)
    assert mark(cursor) == CursorMark(pos=40, resets=0)
    assert moved(cursor, mark(cursor), 41)
    assert mark(place()) == CursorMark(pos=41, resets=0)


@pytest.mark.parametrize(
    "other", [CursorMark(5, 0), CursorMark(0, 1), CursorMark(9, 0)]
)
def test_a_move_from_another_mark_says_no_and_changes_nothing(
    place: Place, other: CursorMark
) -> None:
    cursor = place()
    moved(cursor, mark(cursor), 9)
    if other == CursorMark(9, 0):
        reset(cursor)

    before = mark(cursor)
    assert not moved(cursor, other, 70)
    assert mark(cursor) == before


def test_a_reset_puts_the_position_at_0_and_counts_one_more(place: Place) -> None:
    cursor = place()
    reset(cursor)
    assert mark(cursor) == CursorMark(pos=0, resets=1)
    moved(cursor, mark(cursor), 30)

    reset(cursor)
    assert mark(cursor) == CursorMark(pos=0, resets=2)


def test_a_mark_read_at_0_does_not_move_the_cursor_after_a_reset(place: Place) -> None:
    cursor = place()
    seen = mark(cursor)
    reset(place())

    assert not moved(cursor, seen, 12)
    assert mark(cursor) == CursorMark(pos=0, resets=1)


def test_a_mark_from_before_a_reset_fails_when_the_position_came_back(
    place: Place,
) -> None:
    cursor = place()
    moved(cursor, mark(cursor), 20)
    seen = mark(cursor)
    other = place()
    reset(other)
    moved(other, mark(other), 20)

    assert not moved(cursor, seen, 35)
    assert mark(cursor) == CursorMark(pos=20, resets=1)


def test_two_handles_see_each_others_moves_and_only_one_of_two_moves_wins(
    place: Place,
) -> None:
    first, second = place(), place()
    seen = mark(first)

    assert moved(first, seen, 10)
    assert mark(second) == CursorMark(pos=10, resets=0)
    assert not moved(second, seen, 15)
    assert mark(first) == CursorMark(pos=10, resets=0)
