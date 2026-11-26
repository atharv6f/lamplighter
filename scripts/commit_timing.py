"""Times a commit to the store on this machine, with the Mac's full flush on and off.

Run it from the repo's root with `uv run python scripts/commit_timing.py`.
It writes only inside a new temporary folder, which it removes at the end.
The numbers decide whether the store's work stays on the event loop's thread,
so they are only worth reading from the machine the agent runs on.
"""

import asyncio
import fcntl
import os
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from lamplighter.adapters import FileBlobStore, SystemClock
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.ports import Failure, NewEvent

AT = "2026-11-26T07:35:12.345Z"
COMMITS = 500
BARE_WRITES = 200
PUTS = 20
EVENT_BYTES = 2 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_BLOB_BYTES = 64 * 1024 * 1024
# The saved payload is {"text":"..."}, which adds 11 bytes to the text.
LARGEST_TEXT = MAX_RECORD_BYTES - 11
SETTINGS = (
    "journal_mode",
    "synchronous",
    "fullfsync",
    "checkpoint_fullfsync",
    "secure_delete",
    "busy_timeout",
    "application_id",
    "user_version",
)
_FULL_FLUSH = getattr(fcntl, "F_FULLFSYNC", None)


def main() -> None:
    """Print every measurement, then what each rule makes of them."""
    folder = Path(tempfile.mkdtemp(prefix="lamplighter-timing-"))
    try:
        asyncio.run(_measure(folder))
    finally:
        shutil.rmtree(folder)


async def _measure(folder: Path) -> None:
    on = _opened(folder / "on")
    print(f"SQLite {sqlite3.sqlite_version}, from {sqlite3.__file__}")
    print("compile options:")
    for (option,) in on.execute("PRAGMA compile_options"):
        print(f"  {option}")
    print("settings as read back:")
    for name in SETTINGS:
        print(f"  {name} = {on.execute(f'PRAGMA {name}').fetchone()[0]}")

    with_flush = await _commit_times(on)
    off = _opened(folder / "off")
    off.execute("PRAGMA fullfsync = OFF")
    without_flush = await _commit_times(off)
    _report(f"commit of a 2 KB event, {COMMITS} times, fullfsync on", with_flush)
    _report(f"commit of a 2 KB event, {COMMITS} times, fullfsync off", without_flush)

    bare_full: list[float] = []
    if _FULL_FLUSH is not None:
        full = _FULL_FLUSH
        bare_full = _bare_times(folder / "bare-full", lambda d: fcntl.fcntl(d, full))
    bare_plain = _bare_times(folder / "bare-plain", os.fsync)
    if bare_full:
        _report(f"bare 2 KB write and F_FULLFSYNC, {BARE_WRITES} times", bare_full)
    else:
        print("bare F_FULLFSYNC: this system has no F_FULLFSYNC")
    _report(f"bare 2 KB write and fsync, {BARE_WRITES} times", bare_plain)

    largest = await _timed(_append_largest(on))
    print(f"commit of one event of {MAX_RECORD_BYTES} bytes: {largest:.2f} ms")
    puts = await _put_times(folder / "blobs")
    for size, times in puts.items():
        _report(f"put of {size} bytes, {PUTS} times", times)
    print(f"F_FULLFSYNC on a folder: {_folder_flush(folder)}")
    print(f"fdesetup status: {_fdesetup()}")
    on.close()
    off.close()
    _rules(with_flush, without_flush, bare_full, bare_plain, puts[1024 * 1024])


def _opened(folder: Path) -> sqlite3.Connection:
    folder.mkdir()
    connection = open_database(folder / "state.db", 5.0)
    if isinstance(connection, Failure):
        raise SystemExit(connection.message)
    return connection


async def _commit_times(connection: sqlite3.Connection) -> list[float]:
    log = SqliteEventLog(connection, {}, MAX_RECORD_BYTES)
    times: list[float] = []
    for seq in range(COMMITS):
        event = NewEvent("timing", 1, {"text": "x" * EVENT_BYTES}, AT)
        times.append(await _timed(log.append("timing", (event,), seq)))
    return times


async def _append_largest(connection: sqlite3.Connection) -> object:
    log = SqliteEventLog(connection, {}, MAX_RECORD_BYTES)
    event = NewEvent("timing", 1, {"text": "x" * LARGEST_TEXT}, AT)
    return await log.append("largest", (event,), 0)


async def _put_times(folder: Path) -> dict[int, list[float]]:
    folder.mkdir()
    blobs = FileBlobStore(folder, SystemClock(), MAX_BLOB_BYTES)
    times: dict[int, list[float]] = {}
    for size in (64 * 1024, 1024 * 1024):
        # Each blob differs, so no put finds its blob already there.
        datas = [os.urandom(size) for _ in range(PUTS)]
        times[size] = [
            await _timed(blobs.put(data, "application/octet-stream")) for data in datas
        ]
    return times


async def _timed(work: Awaitable[object]) -> float:
    # A coroutine does nothing until it is awaited, so making it costs no time.
    start = time.perf_counter()
    result = await work
    elapsed = (time.perf_counter() - start) * 1000
    if isinstance(result, Failure):
        raise SystemExit(result.message)
    return elapsed


def _bare_times(path: Path, flush: Callable[[int], object]) -> list[float]:
    data = os.urandom(EVENT_BYTES)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    times: list[float] = []
    try:
        for _ in range(BARE_WRITES):
            start = time.perf_counter()
            os.write(descriptor, data)
            flush(descriptor)
            times.append((time.perf_counter() - start) * 1000)
    finally:
        os.close(descriptor)
    return times


def _folder_flush(folder: Path) -> str:
    if _FULL_FLUSH is None:
        return "this system has no F_FULLFSYNC"
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        fcntl.fcntl(descriptor, _FULL_FLUSH)
        return "accepted"
    except OSError as error:
        return f"refused: {error}"
    finally:
        os.close(descriptor)


def _fdesetup() -> str:
    if shutil.which("fdesetup") is None:
        return "not on this system"
    done = subprocess.run(
        ["fdesetup", "status"], capture_output=True, text=True, check=False
    )
    return (done.stdout + done.stderr).strip()


def _report(what: str, times: list[float]) -> None:
    ordered = sorted(times)
    print(
        f"{what}: lowest {ordered[0]:.2f} ms,"
        f" median {statistics.median(ordered):.2f},"
        f" p95 {_percentile(ordered, 95):.2f},"
        f" p99 {_percentile(ordered, 99):.2f},"
        f" highest {ordered[-1]:.2f}"
    )


def _percentile(ordered: list[float], percent: int) -> float:
    # The nearest rank: the smallest value with at least this share at or below it.
    rank = max(1, -(-len(ordered) * percent // 100))
    return ordered[rank - 1]


def _rules(
    with_flush: list[float],
    without_flush: list[float],
    bare_full: list[float],
    bare_plain: list[float],
    large_puts: list[float],
) -> None:
    on = statistics.median(with_flush)
    off = statistics.median(without_flush)
    print("what the rules make of it:")
    if on >= 2 * off:
        print(f"  the flush is real: the median on is {on / off:.1f} times off")
    elif bare_full and statistics.median(bare_full) >= 2 * statistics.median(
        bare_plain
    ):
        print("  SQLITE IS NOT MAKING THE FULL FLUSH: switch Python's SQLite build")
    else:
        print("  the drive makes both flushes cost the same: a fact about the drive")
    p99 = _percentile(sorted(with_flush), 99)
    if p99 > 100:
        print(f"  p99 on is {p99:.2f} ms, over 100: MOVE SQLite calls to a thread")
    else:
        print(f"  p99 on is {p99:.2f} ms, not over 100: SQLite stays on the loop")
    put = max(large_puts)
    if put > 100:
        print(f"  slowest 1 MiB put is {put:.2f} ms, over 100: MOVE it to a thread")
    else:
        print(f"  slowest 1 MiB put is {put:.2f} ms, not over 100: it stays")
    if on > 25:
        print(f"  median on is {on:.2f} ms, over 25: sharing commits is worth it")
    else:
        print(f"  median on is {on:.2f} ms, not over 25: commits stay separate")


if __name__ == "__main__":
    main()
