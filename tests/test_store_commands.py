"""The four store commands, run through the entry point as a person would."""

import asyncio
import os
from pathlib import Path

import pytest

from lamplighter.adapters import FileBlobStore, SystemClock
from lamplighter.adapters.sqlite import SqliteEffectLedger, SqliteEventLog
from lamplighter.app.main import main
from lamplighter.ports import BlobHandle, NewEvent, to_plain
from tests.sqlite.helpers import AT, note, opened
from tests.store_samples import TIMED_OUT, action

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"


def settings_for(tmp_path: Path) -> Path:
    text = SETTINGS.read_text().replace(
        'dir = "~/.lamplighter"', f'dir = "{tmp_path / "store"}"'
    )
    path = tmp_path / "lamplighter.toml"
    path.write_text(text)
    return path


def make_store(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    connection = opened(folder / "state.db")
    log = SqliteEventLog(connection, {}, 10_000)
    ledger = SqliteEffectLedger(connection, SystemClock(), 10_000)
    blobs = FileBlobStore(folder / "blobs", SystemClock(), 10_000)

    async def fill() -> None:
        handle = await blobs.put(b"a made-up list", "text/plain")
        assert isinstance(handle, BlobHandle)
        held = NewEvent(type="note", v=1, payload={"file": to_plain(handle)}, at=AT)
        await log.append("sess-a", [note(), held], 0)
        started = NewEvent(type="run_started", v=1, payload={}, at=AT, run_id="r-1")
        await log.append("sess-b", [started], 0)
        await ledger.begin(action("sess-b"), None, None)
        await ledger.finish(1, TIMED_OUT)

    asyncio.run(fill())
    connection.close()


def store_command(tmp_path: Path, *words: str) -> int:
    return main(["--config", str(settings_for(tmp_path)), "store", *words])


def test_a_command_on_a_folder_with_no_store_makes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert store_command(tmp_path, "sessions") == 1
    assert "no store" in capsys.readouterr().err
    assert not (tmp_path / "store").exists()


def test_sessions_lists_each_session_with_its_state_and_unsettled_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_store(tmp_path)
    assert store_command(tmp_path, "sessions") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1:] == [f"sess-a  2  {AT}  idle  0", f"sess-b  1  {AT}  unfinished  1"]


def test_sizes_reports_the_tables_and_the_blobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_store(tmp_path)
    assert store_command(tmp_path, "sizes") == 0
    out = capsys.readouterr().out
    assert "events: 3 rows" in out
    assert "blobs: 1, 14 bytes" in out
    assert "sess-a: 2 events" in out


def test_export_writes_a_folder_and_refuses_one_that_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_store(tmp_path)
    out = tmp_path / "copy"
    assert store_command(tmp_path, "export", "sess-a", str(out)) == 0
    assert (out / "manifest.json").exists()
    assert "Exported 2 events" in capsys.readouterr().out
    assert store_command(tmp_path, "export", "sess-a", str(out)) == 1
    assert "already exists" in capsys.readouterr().err


def test_delete_exports_then_removes_the_session_and_its_unused_blob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_store(tmp_path)
    blobs = tmp_path / "store" / "blobs"
    # Makes the blob older than the sweep's hour, as if it was put long ago.
    for path in blobs.iterdir():
        if not path.name.startswith("."):
            old = path.stat().st_mtime - 7_200
            os.utime(path, (old, old))
    out = tmp_path / "copy"
    assert store_command(tmp_path, "delete", "sess-a", "--export-to", str(out)) == 0
    assert "Removed 1 blobs" in capsys.readouterr().out
    assert (out / "manifest.json").exists()
    assert [p.name for p in blobs.iterdir() if not p.name.startswith(".")] == []
    assert store_command(tmp_path, "sessions") == 0
    assert "sess-a" not in capsys.readouterr().out


def test_delete_refuses_a_session_whose_run_has_not_ended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_store(tmp_path)
    out = tmp_path / "copy"
    assert store_command(tmp_path, "delete", "sess-b", "--export-to", str(out)) == 1
    assert "did not end" in capsys.readouterr().err
    assert not out.exists()
