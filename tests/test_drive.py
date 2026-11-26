"""Forcing files and folders to the drive."""

import os
import stat
from pathlib import Path

import pytest

from lamplighter.adapters import FileBlobStore, drive
from tests.fake_clock import FakeClock
from tests.test_file_blob_store import LIST, put


def flushes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record each flush the drive helper makes, as "file" or "folder"."""
    seen: list[str] = []

    def record(descriptor: int) -> None:
        is_folder = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        seen.append("folder" if is_folder else "file")

    # The full flush is the Mac's. Where it is missing or refused, the helper
    # falls back to a plain fsync, so recording that sees every flush.
    monkeypatch.setattr(drive, "_FULL_FLUSH", None)
    monkeypatch.setattr(drive.os, "fsync", record)
    return seen


def test_a_put_forces_the_blob_and_then_its_folder_to_the_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = flushes(monkeypatch)
    put(FileBlobStore(tmp_path / "blobs", FakeClock(), 1_000), LIST)
    assert seen == ["file", "folder"]


def test_a_refused_full_flush_falls_back_to_a_plain_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = flushes(monkeypatch)

    def refuse(*_: object) -> None:
        raise OSError("not supported")

    monkeypatch.setattr(drive, "_FULL_FLUSH", 51)
    monkeypatch.setattr(drive.fcntl, "fcntl", refuse)
    drive.write_flushed(tmp_path / "a-file", b"tea")
    assert seen == ["file"]
