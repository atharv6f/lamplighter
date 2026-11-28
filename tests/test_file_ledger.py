"""The book in a file: a kill leaves a row whole or absent, and processes take turns."""

import asyncio
import multiprocessing
import os
import random
import signal
import stat
import time
from collections.abc import Callable, Sequence
from multiprocessing.queues import Queue
from pathlib import Path
from typing import cast

import pytest

from lamplighter.adapters import FileLedger
from lamplighter.ports import Failure, LedgerRow, row_text
from tests.ledger_kit import (
    add_loose_rows,
    append_forever,
    loose,
    plan,
    plan_each_round,
    whole_experiment,
)

PROCESSES = 8


def _book(tmp_path: Path) -> tuple[FileLedger, Path]:
    path = tmp_path / "ledger.jsonl"
    return FileLedger(path), path


def _rows(book: FileLedger) -> Sequence[LedgerRow]:
    found = asyncio.run(book.rows())
    assert not isinstance(found, Failure), found
    return found


def _added(book: FileLedger, *rows: LedgerRow) -> None:
    for row in rows:
        assert asyncio.run(book.append(row)) is None


def test_each_row_is_one_line_of_canonical_json(tmp_path: Path) -> None:
    book, path = _book(tmp_path)
    every = whole_experiment()
    _added(book, *every)
    expected = "".join(row_text(r) + "\n" for r in every)
    assert path.read_text(encoding="utf-8") == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("cut", [1, 2, 17, -40, -1])
def test_a_torn_last_row_is_left_out_by_a_reader_and_cut_by_a_writer(
    tmp_path: Path, cut: int
) -> None:
    book, path = _book(tmp_path)
    kept = plan("kept")
    _added(book, kept)
    torn = row_text(plan("torn")).encode()[:cut]
    with path.open("ab") as out:
        out.write(torn)
    before = path.read_bytes()
    assert list(_rows(book)) == [kept]
    assert path.read_bytes() == before
    after = plan("after")
    _added(book, after)
    assert list(_rows(book)) == [kept, after]
    assert (
        path.read_text(encoding="utf-8")
        == row_text(kept) + "\n" + row_text(after) + "\n"
    )


def test_a_whole_last_row_with_no_newline_counts(tmp_path: Path) -> None:
    book, path = _book(tmp_path)
    first, second = plan("first"), plan("second")
    path.write_text(row_text(first) + "\n" + row_text(second), encoding="utf-8")
    assert list(_rows(book)) == [first, second]
    third = plan("third")
    _added(book, third)
    assert list(_rows(book)) == [first, second, third]
    assert path.read_text(encoding="utf-8").count("\n") == 3


@pytest.mark.parametrize(
    "line",
    [
        '{"v": 2}',
        "not json",
        row_text(plan()).replace('"v":1', '"v":2'),
    ],
)
def test_a_whole_line_that_is_not_a_row_makes_the_book_unavailable(
    tmp_path: Path, line: str
) -> None:
    book, path = _book(tmp_path)
    _added(book, plan("first"))
    with path.open("a", encoding="utf-8") as out:
        out.write(line + "\n")
    before = path.read_bytes()
    found = asyncio.run(book.rows())
    assert isinstance(found, Failure)
    assert found.kind == "unavailable"
    assert "Line 2" in found.message
    answer = asyncio.run(book.append(plan("second")))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"
    assert path.read_bytes() == before


def test_a_book_in_a_folder_that_is_not_there_is_unavailable(tmp_path: Path) -> None:
    book = FileLedger(tmp_path / "gone" / "ledger.jsonl")
    for answer in (asyncio.run(book.append(plan())), asyncio.run(book.rows())):
        assert isinstance(answer, Failure)
        assert answer.kind == "unavailable"


def test_a_book_that_is_a_folder_is_unavailable(tmp_path: Path) -> None:
    book, path = _book(tmp_path)
    path.mkdir()
    answer = asyncio.run(book.append(plan()))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"


def _at_once(target: Callable[..., None], *args: object) -> list[object]:
    # Every process waits at one barrier, so that their appends meet.
    spawn = multiprocessing.get_context("spawn")
    barrier = spawn.Barrier(PROCESSES)
    answers: Queue[object] = spawn.Queue()
    workers = [
        spawn.Process(target=target, args=(*args, barrier, answers))
        for _ in range(PROCESSES)
    ]
    for worker in workers:
        worker.start()
    found = [answers.get(timeout=600) for _ in workers]
    for worker in workers:
        worker.join()
        assert worker.exitcode == 0
    return found


def test_a_kill_during_appends_leaves_every_row_whole(tmp_path: Path) -> None:
    book, path = _book(tmp_path)
    draw = random.Random(9)
    spawn = multiprocessing.get_context("spawn")
    for _ in range(12):
        size = path.stat().st_size if path.exists() else 0
        writer = spawn.Process(target=append_forever, args=(str(path),))
        writer.start()
        # Wait until it has written, then kill it at a random moment.
        while (path.stat().st_size if path.exists() else 0) <= size:
            assert writer.is_alive()
            time.sleep(0.005)
        time.sleep(draw.uniform(0.0, 0.05))
        assert writer.pid is not None
        os.kill(writer.pid, signal.SIGKILL)
        writer.join()
        before = len(_rows(book))
        _added(book, loose(f"after-kill-{before}"))
        assert len(_rows(book)) == before + 1
    assert len(_rows(book)) > 24


def test_processes_that_plan_one_experiment_at_once_let_exactly_one_in(
    tmp_path: Path,
) -> None:
    # Without the lock, a break showed in only 1 or 2 of 20 rounds, so the
    # test runs 200.
    _, path = _book(tmp_path)
    rounds = 200
    # Each process puts the list of its answers, one per round.
    taken = cast(list[list[bool]], _at_once(plan_each_round, str(path), rounds))
    for number in range(rounds):
        assert sum(answers[number] for answers in taken) == 1, number


def test_processes_that_add_their_own_rows_at_once_all_get_in(
    tmp_path: Path,
) -> None:
    book, path = _book(tmp_path)
    each = 50
    counts = cast(list[int], _at_once(add_loose_rows, str(path), each))
    assert counts == [each] * PROCESSES
    found = _rows(book)
    assert len(found) == each * PROCESSES
    assert len({r.row_id for r in found}) == each * PROCESSES


def test_each_append_forces_the_book_to_the_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A row that is not forced down can be lost to a power cut, which no
    # test can make, so the call itself is checked.
    book, path = _book(tmp_path)
    forced: list[int] = []

    def flush(descriptor: int) -> None:
        forced.append(os.fstat(descriptor).st_ino)

    monkeypatch.setattr("lamplighter.adapters.file_ledger.flush", flush)
    _added(book, plan("one"), plan("two"))
    assert forced == [path.stat().st_ino] * 2
