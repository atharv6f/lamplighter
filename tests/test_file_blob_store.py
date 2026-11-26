"""What the file blob store does beyond the port's contract tests."""

import asyncio
import errno
import fcntl
import hashlib
import os
import stat
import threading
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import BlobTotals, FileBlobStore
from lamplighter.ports import BlobHandle, Failure
from tests.fake_clock import FakeClock

LIST = b"milk\neggs\ntea\n"


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def put(store: FileBlobStore, data: bytes) -> BlobHandle:
    handle = run(store.put(data, "text/plain"))
    assert isinstance(handle, BlobHandle), handle
    return handle


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


def test_a_blob_is_a_file_named_by_its_hash_that_only_its_owner_can_read(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "blobs"
    handle = put(FileBlobStore(folder, FakeClock(), 1_000), LIST)
    path = folder / handle.sha256
    assert path.read_bytes() == LIST
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert sorted(p.name for p in folder.iterdir()) == [".lock", handle.sha256]


def test_a_blob_whose_bytes_changed_does_not_match_its_hash(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path, FakeClock(), 1_000)
    handle = put(store, LIST)
    (tmp_path / handle.sha256).write_bytes(b"milk\neggs\ntex\n")
    failure = failure_of(run(store.get(handle)))
    assert failure.kind == "fatal"
    assert failure.detail["problem"] == "hash"


def test_a_blob_cut_short_does_not_match_its_size(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path, FakeClock(), 1_000)
    handle = put(store, LIST)
    (tmp_path / handle.sha256).write_bytes(LIST[:4])
    assert failure_of(run(store.get(handle))).detail["problem"] == "size"


def test_a_removed_blob_is_missing(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path, FakeClock(), 1_000)
    handle = put(store, LIST)
    (tmp_path / handle.sha256).unlink()
    assert failure_of(run(store.get(handle))).detail["problem"] == "missing"


def test_a_second_put_repairs_a_damaged_blob(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path, FakeClock(), 1_000)
    handle = put(store, LIST)
    (tmp_path / handle.sha256).write_bytes(b"damaged")
    put(store, LIST)
    assert run(store.get(handle)) == LIST


def test_a_put_stamps_the_file_with_the_clock_it_was_given(tmp_path: Path) -> None:
    clock = FakeClock()
    handle = put(FileBlobStore(tmp_path, clock, 1_000), LIST)
    assert (tmp_path / handle.sha256).stat().st_mtime == clock.now().timestamp()


def sweep(store: FileBlobStore, in_use: set[str]) -> int:
    removed = store.remove_unused(in_use)
    assert isinstance(removed, int), removed
    return removed


def test_the_sweep_removes_only_old_blobs_that_nothing_uses(tmp_path: Path) -> None:
    clock = FakeClock()
    store = FileBlobStore(tmp_path, clock, 1_000)
    used = put(store, b"used")
    unused = put(store, b"unused")
    clock.wall += timedelta(minutes=30)
    young = put(store, b"young")
    clock.wall += timedelta(minutes=31)
    assert sweep(store, {used.sha256}) == 1
    names = {p.name for p in tmp_path.iterdir()}
    assert used.sha256 in names
    assert young.sha256 in names
    assert unused.sha256 not in names


def test_a_blob_put_again_is_safe_from_the_sweep_for_another_hour(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = FileBlobStore(tmp_path, clock, 1_000)
    put(store, LIST)
    clock.wall += timedelta(minutes=59)
    put(store, LIST)
    clock.wall += timedelta(minutes=59)
    assert sweep(store, set()) == 0


def test_the_sweep_removes_old_leftovers_of_killed_puts(tmp_path: Path) -> None:
    clock = FakeClock()
    store = FileBlobStore(tmp_path, clock, 1_000)
    put(store, LIST)
    old = tmp_path / f".{'a' * 64}.4242.tmp"
    old.write_bytes(b"half")
    hour_ago = clock.now().timestamp() - 3_601
    os.utime(old, (hour_ago, hour_ago))
    clock.wall += timedelta(minutes=61)
    fresh = tmp_path / f".{'b' * 64}.4243.tmp"
    fresh.write_bytes(b"half")
    os.utime(fresh, (clock.now().timestamp(), clock.now().timestamp()))
    assert sweep(store, {hashlib.sha256(LIST).hexdigest()}) == 1
    assert not old.exists()
    assert fresh.exists()
    assert (tmp_path / ".lock").exists()


def waits_for_the_lock(folder: Path, how: int, work: Callable[[], object]) -> None:
    """Hold the folder's lock as `how`, and check that `work` waits for it."""
    folder.mkdir(exist_ok=True)
    descriptor = os.open(folder / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    done = threading.Event()

    def in_thread() -> None:
        work()
        done.set()

    try:
        fcntl.flock(descriptor, how)
        thread = threading.Thread(target=in_thread)
        thread.start()
        assert not done.wait(0.3), "the work did not wait for the lock"
    finally:
        os.close(descriptor)
    thread.join(5)
    assert done.is_set(), "the work never finished once the lock was free"


def test_a_put_waits_while_the_sweep_holds_the_lock(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path / "blobs", FakeClock(), 1_000)
    waits_for_the_lock(tmp_path / "blobs", fcntl.LOCK_EX, lambda: put(store, LIST))


def test_the_sweep_waits_while_a_put_holds_the_lock(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path / "blobs", FakeClock(), 1_000)
    waits_for_the_lock(
        tmp_path / "blobs", fcntl.LOCK_SH, lambda: store.remove_unused(frozenset())
    )


def test_the_totals_count_only_blobs(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path / "blobs", FakeClock(), 1_000)
    assert store.totals() == BlobTotals(count=0, total_bytes=0)
    put(store, b"one")
    put(store, b"three")
    (tmp_path / "blobs" / ".x.1.tmp").write_bytes(b"leftover")
    assert store.totals() == BlobTotals(count=2, total_bytes=8)


def test_a_folder_that_cannot_be_made_is_fatal(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    store = FileBlobStore(blocker / "blobs", FakeClock(), 1_000)
    assert failure_of(run(store.put(LIST, "text/plain"))).kind == "fatal"


def test_a_full_drive_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("lamplighter.adapters.file_blob_store.write_flushed", full)
    store = FileBlobStore(tmp_path / "blobs", FakeClock(), 1_000)
    failure = failure_of(run(store.put(LIST, "text/plain")))
    assert (failure.kind, failure.effect) == ("unavailable", "none")
