"""Watches a server's memory and the Mac's memory pressure while the bake-off asks it.

The footprint is the `phys_footprint` that `footprint` prints, read from
the kernel with `proc_pid_rusage`, and the pressure level is
`kern.memorystatus_vm_pressure_level`, read with `sysctlbyname`. Both are
read in this process, so a read starts no program and takes microseconds.
"""

import ctypes
import ctypes.util
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from lamplighter.ports import Clock

# `rusage_info_v2` starts with a 16-byte uuid and then 64-bit counts, of
# which `ri_phys_footprint` is the eighth. Room is left for the longer
# layouts the call may write.
_RUSAGE_INFO_V2 = 2
_FOOTPRINT_OFFSET = 16 + 7 * 8
_RUSAGE_BYTES = 512
_PRESSURE = b"kern.memorystatus_vm_pressure_level"


@dataclass(frozen=True, slots=True)
class MemoryPeaks:
    """The peaks the watch saw, how many reads it made, and the longest gap between two.

    `unread` counts the reads that gave nothing, such as after the server
    stopped.
    """

    footprint_bytes: int
    pressure_level: int
    reads: int
    unread: int
    longest_gap_ms: int


class MemoryWatch:
    """Reads the footprint and the pressure as often as it can, at most each second."""

    def __init__(
        self,
        footprint: Callable[[], int | None],
        pressure: Callable[[], int | None],
        clock: Clock,
    ) -> None:
        """Keep the two readers and the clock that paces them."""
        self._footprint = footprint
        self._pressure = pressure
        self._clock = clock
        self._peak = 0
        self._level = 0
        self._reads = 0
        self._unread = 0
        self._last: float | None = None
        self._longest = 0.0

    async def watch(self) -> None:
        """Read until cancelled."""
        while True:
            started = self._clock.monotonic()
            self.read_once()
            await self._clock.sleep(max(0.0, 1.0 - (self._clock.monotonic() - started)))

    def read_once(self) -> None:
        """Read both values once, and keep the peaks."""
        now = self._clock.monotonic()
        if self._last is not None:
            self._longest = max(self._longest, now - self._last)
        self._last = now
        footprint, level = self._footprint(), self._pressure()
        self._reads += 1
        if footprint is None or level is None:
            self._unread += 1
            return
        self._peak = max(self._peak, footprint)
        self._level = max(self._level, level)

    def peaks(self) -> MemoryPeaks:
        """Give what the watch saw so far."""
        return MemoryPeaks(
            footprint_bytes=self._peak,
            pressure_level=self._level,
            reads=self._reads,
            unread=self._unread,
            longest_gap_ms=round(self._longest * 1000),
        )


def listening_pid(port: int, seconds: float) -> int | None:
    """Give the id of the process listening on 127.0.0.1 at `port`, or None."""
    try:
        found = subprocess.run(
            ["/usr/sbin/lsof", "-nP", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    ids = found.stdout.split()
    return int(ids[0]) if found.returncode == 0 and ids and ids[0].isdigit() else None


def phys_footprint(pid: int) -> Callable[[], int | None]:
    """Give a reader of the process's `phys_footprint`, None once it is gone."""
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")

    def read() -> int | None:
        buffer = ctypes.create_string_buffer(_RUSAGE_BYTES)
        if libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V2, buffer) != 0:
            return None
        return int.from_bytes(
            buffer.raw[_FOOTPRINT_OFFSET : _FOOTPRINT_OFFSET + 8], "little"
        )

    return read


def pressure_level() -> int | None:
    """Give the Mac's memory pressure level: 1 normal, 2 warning, 4 critical."""
    libc = ctypes.CDLL(None)
    level = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(level))
    if libc.sysctlbyname(_PRESSURE, ctypes.byref(level), ctypes.byref(size), None, 0):
        return None
    return level.value
