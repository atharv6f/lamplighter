"""The file cursor's own rules: bytes, modes, targets, bad files and the lock."""

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FileTraceCursor
from lamplighter.ports import CursorMark, Failure, canonical_json

TARGET = canonical_json(["http://127.0.0.1:6006", "lamplighter", 1])
OTHER = canonical_json(["http://127.0.0.1:6006", "other", 1])
DELETE = "Stop every chat, delete the file and start again."


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def cursor(folder: Path, target: str = TARGET) -> FileTraceCursor:
    return FileTraceCursor(folder, target)


def mark(made: FileTraceCursor) -> CursorMark:
    found = run(made.read())
    assert isinstance(found, CursorMark), found
    return found


def file_of(folder: Path) -> Path:
    return folder / "trace_cursor.json"


def test_a_move_and_a_reset_write_these_exact_bytes(tmp_path: Path) -> None:
    made = cursor(tmp_path)
    assert run(made.move(CursorMark(0, 0), 4180)) is True
    assert file_of(tmp_path).read_bytes() == (
        b'{"cursors":{"[\\"http://127.0.0.1:6006\\",\\"lamplighter\\",1]":'
        b'{"pos":4180,"resets":0}},"v":1}'
    )

    assert run(made.reset()) is None
    assert file_of(tmp_path).read_bytes() == (
        b'{"cursors":{"[\\"http://127.0.0.1:6006\\",\\"lamplighter\\",1]":'
        b'{"pos":0,"resets":1}},"v":1}'
    )


def test_both_files_are_for_their_owner_only(tmp_path: Path) -> None:
    run(cursor(tmp_path).move(CursorMark(0, 0), 3))

    for name in ("trace_cursor.json", "trace_cursor.lock"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600, name


def test_each_target_reads_moves_and_resets_only_its_own_entry(tmp_path: Path) -> None:
    first, second = cursor(tmp_path), cursor(tmp_path, OTHER)
    run(first.move(CursorMark(0, 0), 300))
    run(first.reset())
    run(first.move(CursorMark(0, 1), 40))

    assert mark(second) == CursorMark(0, 0)
    assert run(second.move(CursorMark(0, 0), 10)) is True
    assert mark(first) == CursorMark(40, 1)
    run(second.reset())
    assert mark(first) == CursorMark(40, 1)
    assert mark(second) == CursorMark(0, 1)


def test_an_empty_file_is_a_new_cursor_and_a_move_writes_a_good_one(
    tmp_path: Path,
) -> None:
    file_of(tmp_path).write_bytes(b"")
    made = cursor(tmp_path)

    assert mark(made) == CursorMark(0, 0)
    assert run(made.move(CursorMark(0, 0), 7)) is True
    assert mark(cursor(tmp_path)) == CursorMark(7, 0)


GOOD = {"pos": 300, "resets": 1}
BAD: dict[str, object] = {
    "another version": {"cursors": {OTHER: GOOD}, "v": 2},
    "a version that is true": {"cursors": {OTHER: GOOD}, "v": True},
    "a third key": {"cursors": {OTHER: GOOD}, "v": 1, "more": 0},
    "no version": {"cursors": {OTHER: GOOD}},
    "cursors that are a list": {"cursors": [], "v": 1},
    "a position that is text": {"cursors": {TARGET: {"pos": "3", "resets": 0}}, "v": 1},
    "a position that is true": {
        "cursors": {TARGET: {"pos": True, "resets": 0}},
        "v": 1,
    },
    "a negative position": {"cursors": {TARGET: {"pos": -1, "resets": 0}}, "v": 1},
    "a position too large": {
        "cursors": {TARGET: {"pos": 9_223_372_036_854_775_808, "resets": 0}},
        "v": 1,
    },
    "a count that is a float": {"cursors": {OTHER: {"pos": 1, "resets": 0.5}}, "v": 1},
    "an entry with a third key": {
        "cursors": {OTHER: {"pos": 1, "resets": 0, "at": 2}},
        "v": 1,
    },
    "an entry of another target that is bad": {
        "cursors": {TARGET: GOOD, OTHER: {"pos": -1, "resets": 0}},
        "v": 1,
    },
}


def bad_bytes() -> dict[str, bytes]:
    made = {name: json.dumps(data).encode() for name, data in BAD.items()}
    whole = canonical_json({"cursors": {OTHER: GOOD}, "v": 1}).encode()
    made["a file cut in half"] = whole[: len(whole) // 2]
    made["a list"] = b"[1, 2]"
    made["bytes that are not text"] = b"\xff\xfe"
    made["a number too long to convert"] = (
        b'{"cursors":{"t":{"pos":' + b"9" * 5000 + b',"resets":0}},"v":1}'
    )
    made["nesting too deep to read"] = b"[" * 100_000
    key = json.dumps(TARGET).encode()
    twice = key + b':{"pos":300,"resets":1},' + key + b':{"pos":0,"resets":0}'
    made["a target given twice"] = b'{"cursors":{' + twice + b'},"v":1}'
    return made


@pytest.mark.parametrize(("name", "data"), bad_bytes().items(), ids=bad_bytes())
def test_a_file_in_another_shape_is_unavailable_and_never_written_over(
    tmp_path: Path, name: str, data: bytes
) -> None:
    file_of(tmp_path).write_bytes(data)
    made = cursor(tmp_path)

    for result in (
        run(made.read()),
        run(made.move(CursorMark(0, 0), 5)),
        run(made.move(CursorMark(300, 1), 5)),
        run(made.reset()),
    ):
        assert isinstance(result, Failure), name
        assert result.kind == "unavailable"
        assert DELETE in result.message
        assert str(tmp_path) in result.message
    assert file_of(tmp_path).read_bytes() == data


def test_a_write_that_takes_only_part_of_the_bytes_is_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole_write = os.write

    def part(descriptor: int, data: bytes) -> int:
        # A nearly full disk may take a few bytes of a write and no more.
        return whole_write(descriptor, bytes(data)[:7])

    made = cursor(tmp_path)
    assert run(made.move(CursorMark(0, 0), 300)) is True
    monkeypatch.setattr(os, "write", part)

    assert run(made.move(CursorMark(300, 0), 301)) is True
    monkeypatch.undo()
    assert mark(made) == CursorMark(301, 0)


def test_a_leftover_new_file_does_not_stop_a_move(tmp_path: Path) -> None:
    leftover = tmp_path / "trace_cursor.json.new"
    leftover.write_bytes(b'{"half')
    leftover.chmod(0o644)
    made = cursor(tmp_path)

    assert run(made.move(CursorMark(0, 0), 9)) is True
    assert mark(made) == CursorMark(9, 0)
    assert not leftover.exists()
    assert stat.S_IMODE(file_of(tmp_path).stat().st_mode) == 0o600


def test_a_cursor_that_cannot_be_written_or_read_is_unavailable(
    tmp_path: Path,
) -> None:
    # A folder where a file should be: it cannot be opened as one, even by
    # a user who may write anywhere.
    (tmp_path / "trace_cursor.json.new").mkdir()
    made = cursor(tmp_path)

    for result in (run(made.move(CursorMark(0, 0), 3)), run(made.reset())):
        assert isinstance(result, Failure)
        assert result.kind == "unavailable"
        assert result.message == (
            f"The trace cursor in {tmp_path} cannot be read or written."
        )
    assert mark(made) == CursorMark(0, 0)

    file_of(tmp_path).mkdir()
    read = run(made.read())
    assert isinstance(read, Failure)
    assert read.kind == "unavailable"


def test_a_lock_file_that_cannot_be_opened_makes_a_move_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "trace_cursor.lock").mkdir()

    moved = run(cursor(tmp_path).move(CursorMark(0, 0), 3))
    assert isinstance(moved, Failure)
    assert moved.kind == "unavailable"


def test_a_reset_with_no_store_folder_makes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "no-store"

    assert run(cursor(folder).reset()) is None
    assert not folder.exists()


HOLDER = """
import fcntl, os, sys, time
descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(60)
"""


def test_a_move_waits_for_the_lock_and_goes_on_when_its_holder_is_killed(
    tmp_path: Path,
) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path / "trace_cursor.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline() == "held\n"
    results: list[bool | Failure] = []
    mover = threading.Thread(
        target=lambda: results.append(run(cursor(tmp_path).move(CursorMark(0, 0), 8)))
    )
    mover.start()
    mover.join(0.5)
    assert mover.is_alive()

    os.kill(holder.pid, signal.SIGKILL)
    holder.wait()
    mover.join(10)
    assert results == [True]
    assert mark(cursor(tmp_path)) == CursorMark(8, 0)


CHANGER = """
import fcntl, os, sys
descriptor = os.open(sys.argv[1] + "/trace_cursor.lock", os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
with open(sys.argv[1] + "/trace_cursor.json", "w") as written:
    written.write(sys.argv[2])
"""


def test_a_move_that_waited_for_the_lock_looks_at_the_mark_only_then(
    tmp_path: Path,
) -> None:
    moved_on = canonical_json({"cursors": {TARGET: {"pos": 50, "resets": 0}}, "v": 1})
    changer = subprocess.Popen(
        [sys.executable, "-c", CHANGER, str(tmp_path), moved_on],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert changer.stdin is not None and changer.stdout is not None
    assert changer.stdout.readline() == "held\n"
    results: list[bool | Failure] = []
    mover = threading.Thread(
        target=lambda: results.append(run(cursor(tmp_path).move(CursorMark(0, 0), 8)))
    )
    mover.start()
    mover.join(0.5)
    changer.stdin.write("go\n")
    changer.stdin.close()
    changer.wait()
    mover.join(10)

    assert results == [False]
    assert mark(cursor(tmp_path)) == CursorMark(50, 0)


def test_a_move_writes_a_new_file_and_renames_it_over_the_old_one(
    tmp_path: Path,
) -> None:
    made = cursor(tmp_path)
    run(made.move(CursorMark(0, 0), 1))
    before = file_of(tmp_path).stat().st_ino
    # Held open, the old file keeps its inode, so the new one cannot reuse it.
    with file_of(tmp_path).open("rb") as old:
        run(made.move(CursorMark(1, 0), 2))
        assert (
            old.read()
            == canonical_json(
                {"cursors": {TARGET: {"pos": 1, "resets": 0}}, "v": 1}
            ).encode()
        )

    assert file_of(tmp_path).stat().st_ino != before
