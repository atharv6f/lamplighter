"""Export, delete, the list of blob hashes in use, and the size report."""

import dataclasses
import errno
import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from lamplighter.adapters import FileBlobStore
from lamplighter.adapters.sqlite import (
    DeleteReport,
    ExportReport,
    SizeReport,
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    delete_session,
    export_session,
    hashes_in_use,
    store_sizes,
)
from lamplighter.ports import (
    Begun,
    BlobHandle,
    DeliveryReceipt,
    Failure,
    NewEvent,
    Outbound,
    event_id_for,
    out_id_for,
    to_plain,
)
from tests.fake_clock import FakeClock
from tests.sqlite.helpers import AT, failure_of, note, opened, run
from tests.store_samples import DONE, TIMED_OUT, action, inbound

LIMIT = 100_000


class Store:
    """A store on a temporary folder, with every adapter on one connection."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.clock = FakeClock()
        self.connection = opened(folder / "state.db")
        self.log = SqliteEventLog(self.connection, {}, LIMIT)
        self.ledger = SqliteEffectLedger(self.connection, self.clock, LIMIT)
        self.inbox = SqliteInbox(self.connection, LIMIT)
        self.blobs = FileBlobStore(folder / "blobs", self.clock, LIMIT)
        self.outbox = SqliteOutbox(self.connection, self.clock, LIMIT)

    def put(self, data: bytes) -> BlobHandle:
        handle = run(self.blobs.put(data, "text/plain"))
        assert isinstance(handle, BlobHandle), handle
        return handle

    def append(self, session_id: str, *events: NewEvent) -> None:
        infos = run(self.log.read(session_id, 0))
        assert not isinstance(infos, Failure)
        assert isinstance(run(self.log.append(session_id, events, len(infos))), int)

    def export(self, session_id: str, folder: Path) -> ExportReport | Failure:
        return run(
            export_session(self.connection, self.blobs, session_id, folder, self.clock)
        )

    def delete(self, session_id: str, folder: Path) -> DeleteReport | Failure:
        return run(
            delete_session(self.connection, self.blobs, session_id, folder, self.clock)
        )


def holding(handle: BlobHandle) -> NewEvent:
    return NewEvent(type="note", v=1, payload={"file": to_plain(handle)}, at=AT)


def mark(kind: str) -> NewEvent:
    return NewEvent(type=kind, v=1, payload={}, at=AT, run_id="run-1")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    made = Store(tmp_path / "store")
    shared = made.put(b"shared by two sessions")
    own = made.put(b"only in sess-a")
    made.append("sess-a", note("one"), holding(shared), holding(own))
    made.append("sess-b", holding(shared))
    accepted = run(made.inbox.accept(dataclasses.replace(inbound("1"), attachments=())))
    run(made.inbox.accept(dataclasses.replace(inbound("2"), attachments=())))
    assert not isinstance(accepted, Failure)
    run(made.inbox.mark_routed(accepted.inbox_id, "sess-a", 1))
    begun = run(made.ledger.begin(action("sess-a"), None, None))
    assert isinstance(begun, Begun)
    run(made.ledger.finish(begun.effect.effect_id, DONE))
    assert run(made.outbox.enqueue(to_you("sess-a", 3))) is None
    assert run(made.outbox.mark_sent(out_id_for("sess-a", 3), RECEIPT)) is None
    assert run(made.outbox.enqueue(to_you("sess-d", 1))) is None
    return made


RECEIPT = DeliveryReceipt(at=AT)


def to_you(session_id: str, seq: int) -> Outbound:
    return Outbound(
        out_id=out_id_for(session_id, seq),
        session_id=session_id,
        kind="reply",
        text="The kettle is on.",
    )


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_an_export_writes_the_rows_the_blobs_and_a_manifest(
    store: Store, tmp_path: Path
) -> None:
    report = store.export("sess-a", tmp_path / "out")
    assert isinstance(report, ExportReport), report
    out = tmp_path / "out"
    events = lines(out / "events.jsonl")
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[0]["payload"] == {"text": "one"}
    assert events[0]["event_id"] == event_id_for("sess-a", 1)
    (effect,) = lines(out / "effects.jsonl")
    assert effect["state"] == "done"
    assert isinstance(effect["action"], dict)
    (routed,) = lines(out / "inbox.jsonl")
    assert routed["native_id"] == "1"
    assert routed["routed_session_id"] == "sess-a"
    shared = hashlib.sha256(b"shared by two sessions").hexdigest()
    own = hashlib.sha256(b"only in sess-a").hexdigest()
    assert sorted(p.name for p in (out / "blobs").iterdir()) == sorted([shared, own])
    assert (out / "blobs" / own).read_bytes() == b"only in sess-a"
    (sent,) = lines(out / "outbox.jsonl")
    assert sent["state"] == "sent"
    assert sent["outbound"] == to_plain(to_you("sess-a", 3))
    assert sent["receipt"] == {"at": AT}
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["format"] == 2
    assert manifest["session_id"] == "sess-a"
    assert manifest["exported_at"] == AT
    assert (manifest["last_seq"], manifest["events"]) == (3, 3)
    assert (manifest["effects"], manifest["inbox"], manifest["outbox"]) == (1, 1, 1)
    assert sorted(manifest["blobs"]) == sorted([shared, own])
    assert report.outbox == 1
    for name in ("events.jsonl", "effects.jsonl", "inbox.jsonl", "outbox.jsonl"):
        digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert manifest["sha256"][name] == digest
    assert report.last_seq == 3


def test_an_export_refuses_a_folder_that_exists(store: Store, tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    assert failure_of(store.export("sess-a", tmp_path / "out")).kind == "invalid_input"


def test_an_export_of_a_session_that_does_not_exist_writes_nothing(
    store: Store, tmp_path: Path
) -> None:
    assert failure_of(store.export("nobody", tmp_path / "out")).kind == "invalid_input"
    assert not (tmp_path / "out").exists()


def test_an_export_with_a_missing_blob_fails_and_writes_no_manifest(
    store: Store, tmp_path: Path
) -> None:
    own = hashlib.sha256(b"only in sess-a").hexdigest()
    (store.folder / "blobs" / own).unlink()
    assert failure_of(store.export("sess-a", tmp_path / "out")).kind == "fatal"
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_a_blob_named_by_its_hash_alone_stops_the_delete_before_anything(
    store: Store, tmp_path: Path
) -> None:
    # The sweep would keep it while the row lives, and remove it after the
    # delete, so a blob the export cannot copy must stop the delete.
    recipe = store.put(b"the file as it was")
    undo = NewEvent(type="note", v=1, payload={"blob": recipe.sha256}, at=AT)
    store.append("sess-c", undo)
    failure = failure_of(store.delete("sess-c", tmp_path / "out"))
    assert (failure.kind, failure.effect) == ("needs_user", "none")
    assert recipe.sha256 in failure.message
    assert not (tmp_path / "out").exists()
    assert len(events_in(store, "sess-c")) == 1


def test_a_hash_that_names_no_blob_does_not_stop_an_export(
    store: Store, tmp_path: Path
) -> None:
    named = NewEvent(type="note", v=1, payload={"cause": "c" * 64}, at=AT)
    store.append("sess-c", named)
    assert isinstance(store.export("sess-c", tmp_path / "out"), ExportReport)


def test_a_row_whose_json_does_not_read_is_fatal_and_stops_the_delete(
    store: Store, tmp_path: Path
) -> None:
    store.connection.execute(
        "UPDATE events SET payload = '{not json' WHERE session_id = 'sess-b'"
    )
    failure = failure_of(store.delete("sess-b", tmp_path / "out"))
    assert (failure.kind, failure.effect) == ("fatal", "none")
    assert not (tmp_path / "out").exists()


def test_a_full_drive_during_an_export_is_unavailable(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_: object, **__: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("lamplighter.adapters.sqlite.retention.write_flushed", full)
    failure = failure_of(store.export("sess-a", tmp_path / "out"))
    assert (failure.kind, failure.effect) == ("unavailable", "none")


def events_in(store: Store, session_id: str) -> tuple[object, ...]:
    events = run(store.log.read(session_id, 0))
    assert not isinstance(events, Failure), events
    return events


def count(connection: sqlite3.Connection, sql: str) -> int:
    (found,) = connection.execute(sql).fetchone()
    return found


def test_a_delete_removes_the_session_and_keeps_every_other(
    store: Store, tmp_path: Path
) -> None:
    report = store.delete("sess-a", tmp_path / "out")
    assert isinstance(report, DeleteReport), report
    assert report.log_cleared
    assert (tmp_path / "out" / "manifest.json").exists()
    c = store.connection
    assert count(c, "SELECT count(*) FROM events WHERE session_id = 'sess-a'") == 0
    assert count(c, "SELECT count(*) FROM events WHERE session_id = 'sess-b'") == 1
    assert count(c, "SELECT count(*) FROM effects") == 0
    assert count(c, "SELECT count(*) FROM inbox") == 1
    pending = run(store.inbox.pending())
    assert not isinstance(pending, Failure)
    assert [e.inbound.native_id for e in pending] == ["2"]


def test_after_a_delete_the_sweep_removes_only_the_blobs_nothing_uses(
    store: Store, tmp_path: Path
) -> None:
    young = store.put(b"put a minute ago")
    store.clock.wall += timedelta(hours=2)
    store.put(b"put a minute ago")
    report = store.delete("sess-a", tmp_path / "out")
    assert isinstance(report, DeleteReport), report
    in_use = hashes_in_use(store.connection)
    assert not isinstance(in_use, Failure)
    assert store.blobs.remove_unused(in_use) == 1
    left = {p.name for p in (store.folder / "blobs").iterdir()}
    assert hashlib.sha256(b"shared by two sessions").hexdigest() in left
    assert young.sha256 in left
    assert hashlib.sha256(b"only in sess-a").hexdigest() not in left


def test_a_session_whose_run_has_not_ended_is_not_deleted(
    store: Store, tmp_path: Path
) -> None:
    store.append("sess-b", mark("run_started"))
    failure = failure_of(store.delete("sess-b", tmp_path / "out"))
    assert failure.kind == "conflict"
    assert not (tmp_path / "out").exists()
    store.append("sess-b", mark("run_end"))
    assert isinstance(store.delete("sess-b", tmp_path / "out"), DeleteReport)


def test_a_session_with_an_unsettled_effect_is_not_deleted(
    store: Store, tmp_path: Path
) -> None:
    begun = run(store.ledger.begin(action("sess-b"), None, None))
    assert isinstance(begun, Begun)
    run(store.ledger.finish(begun.effect.effect_id, TIMED_OUT))
    failure = failure_of(store.delete("sess-b", tmp_path / "out"))
    assert failure.kind == "conflict"
    assert count(store.connection, "SELECT count(*) FROM events") == 4


def test_an_event_saved_between_the_export_and_the_delete_stops_the_delete(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lamplighter.adapters.sqlite.retention as retention

    real_export = retention.export_session

    async def export_then_write(
        connection: sqlite3.Connection,
        blobs: FileBlobStore,
        session_id: str,
        folder: Path,
        clock: FakeClock,
    ) -> ExportReport | Failure:
        report = await real_export(connection, blobs, session_id, folder, clock)
        # The service writes to the session while the delete is under way.
        assert await store.log.append("sess-a", [note("late")], 3) == 4
        return report

    monkeypatch.setattr(retention, "export_session", export_then_write)
    failure = failure_of(store.delete("sess-a", tmp_path / "out"))
    assert failure.kind == "conflict"
    assert (
        count(
            store.connection, "SELECT count(*) FROM events WHERE session_id = 'sess-a'"
        )
        == 4
    )


def test_a_delete_leaves_no_copy_of_the_rows_in_the_file(
    store: Store, tmp_path: Path
) -> None:
    secret = "a made-up note that must not outlive its session"
    store.append("sess-a", note(secret))
    assert isinstance(store.delete("sess-a", tmp_path / "out"), DeleteReport)
    store.connection.close()
    for name in ("state.db", "state.db-wal"):
        path = store.folder / name
        if path.exists():
            assert secret.encode() not in path.read_bytes(), name


def test_the_hashes_in_use_include_every_hex_run_in_saved_json(store: Store) -> None:
    with_photo = inbound("3")
    run(store.inbox.accept(with_photo))
    in_use = hashes_in_use(store.connection)
    assert not isinstance(in_use, Failure)
    assert hashlib.sha256(b"only in sess-a").hexdigest() in in_use
    assert action("sess-a").effect_key in in_use
    assert with_photo.attachments[0].sha256 in in_use


def test_a_delete_while_another_process_reads_leaves_the_log_for_the_next_open(
    store: Store, tmp_path: Path
) -> None:
    reader = opened(store.folder / "state.db")
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM events").fetchone()
    report = store.delete("sess-b", tmp_path / "out")
    reader.execute("ROLLBACK")
    assert isinstance(report, DeleteReport), report
    assert not report.log_cleared


def test_the_size_report_counts_the_file_the_tables_and_the_sessions(
    store: Store,
) -> None:
    report = store_sizes(store.connection, store.folder / "state.db")
    assert isinstance(report, SizeReport), report
    assert report.file_bytes > 0
    assert report.pages > 0
    assert {t.table: t.rows for t in report.tables} == {
        "events": 4,
        "effects": 1,
        "inbox": 2,
        "outbox": 2,
    }
    assert [s.session_id for s in report.largest_sessions] == ["sess-a", "sess-b"]
    assert report.largest_sessions[0].events == 3


def test_a_delete_removes_the_sessions_outbox_rows_and_keeps_the_others(
    store: Store, tmp_path: Path
) -> None:
    assert isinstance(store.delete("sess-a", tmp_path / "out"), DeleteReport)
    rows = store.connection.execute("SELECT session_id FROM outbox").fetchall()
    assert rows == [("sess-d",)]


def test_a_session_with_a_message_not_yet_sent_is_not_deleted(
    store: Store, tmp_path: Path
) -> None:
    store.append("sess-d", note("waiting"))
    refused = failure_of(store.delete("sess-d", tmp_path / "out"))
    assert refused.kind == "conflict"
    assert "not yet sent" in refused.message
    assert not (tmp_path / "out").exists()
    assert run(store.outbox.mark_sent(out_id_for("sess-d", 1), RECEIPT)) is None
    assert isinstance(store.delete("sess-d", tmp_path / "out"), DeleteReport)


def test_an_outbox_row_sent_between_the_export_and_the_delete_stops_the_delete(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(store.outbox.enqueue(to_you("sess-a", 9))) is None
    assert run(store.outbox.mark_sent(out_id_for("sess-a", 9), RECEIPT)) is None
    import lamplighter.adapters.sqlite.retention as retention

    real_export = retention.export_session

    async def export_then_change(
        connection: sqlite3.Connection,
        blobs: FileBlobStore,
        session_id: str,
        folder: Path,
        clock: FakeClock,
    ) -> ExportReport | Failure:
        report = await real_export(connection, blobs, session_id, folder, clock)
        # A row of the session changes while the delete is under way.
        store.connection.execute(
            "UPDATE outbox SET sent_at = '2026-11-26T08:00:00.000Z' WHERE out_id = ?",
            (out_id_for("sess-a", 9),),
        )
        return report

    monkeypatch.setattr(retention, "export_session", export_then_change)
    refused = failure_of(store.delete("sess-a", tmp_path / "out"))
    assert refused.kind == "conflict"
    assert store.connection.execute("SELECT count(*) FROM outbox").fetchone() == (3,)
