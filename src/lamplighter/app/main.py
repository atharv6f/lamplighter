"""Entry point: builds the adapters and runs a command."""

import argparse
import asyncio
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from lamplighter.adapters import FileBlobStore, SystemClock
from lamplighter.adapters.anthropic import AnthropicModel
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    delete_session,
    export_session,
    hashes_in_use,
    open_database,
    store_sizes,
)
from lamplighter.app.config import Config, load_config
from lamplighter.ports import Failure, ModelClient

# How long a write waits for another process that holds the store's lock.
STORE_TIMEOUT_S = 5.0


def build_model(config: Config, api_key: str) -> ModelClient:
    """Build the client for the model the settings name."""
    return AnthropicModel(config.model, api_key, httpx.AsyncClient(), SystemClock())


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command on the command line, and give the exit code."""
    args = _parser().parse_args(argv)
    if args.command is None:
        print("lamplighter: nothing to run yet")
        return 0
    config = load_config(args.config)
    state = Path(config.store.dir) / "state.db"
    # A store command never makes a store, so a mistyped path cannot leave
    # a new empty one behind.
    if not state.is_file():
        print(f"There is no store at {state}.", file=sys.stderr)
        return 1
    connection = open_database(state, STORE_TIMEOUT_S)
    if isinstance(connection, Failure):
        return _failed(connection)
    try:
        return asyncio.run(_store_command(args, config, connection))
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lamplighter")
    parser.add_argument(
        "--config", type=Path, default=Path("lamplighter.toml"), help="settings file"
    )
    commands = parser.add_subparsers(dest="command")
    store = commands.add_parser("store", help="look after the saved state")
    actions = store.add_subparsers(dest="action", required=True)
    actions.add_parser("sessions", help="list every session")
    actions.add_parser("sizes", help="report how large the store is")
    export = actions.add_parser("export", help="copy one session into a new folder")
    export.add_argument("session")
    export.add_argument("folder", type=Path)
    delete = actions.add_parser("delete", help="export one session, then delete it")
    delete.add_argument("session")
    delete.add_argument("--export-to", type=Path, required=True)
    return parser


async def _store_command(
    args: argparse.Namespace, config: Config, connection: sqlite3.Connection
) -> int:
    clock = SystemClock()
    blobs = FileBlobStore(
        Path(config.store.dir) / "blobs", clock, config.store.max_blob_bytes
    )
    match args.action:
        case "sessions":
            return await _sessions(config, connection)
        case "sizes":
            return _sizes(connection, Path(config.store.dir), blobs)
        case "export":
            report = await export_session(
                connection, blobs, args.session, args.folder, clock
            )
            if isinstance(report, Failure):
                return _failed(report)
            print(
                f"Exported {report.events} events, {report.effects} effects,"
                f" {report.inbox} inbox rows, {report.outbox} outbox rows and"
                f" {len(report.blobs)} blobs"
                f" to {report.folder}."
            )
            return 0
        case _:
            return await _delete(connection, blobs, args.session, args.export_to)


async def _sessions(config: Config, connection: sqlite3.Connection) -> int:
    limit = config.store.max_record_bytes
    infos = await SqliteEventLog(connection, {}, limit).sessions(None)
    if isinstance(infos, Failure):
        return _failed(infos)
    effects = await SqliteEffectLedger(connection, SystemClock(), limit).unresolved()
    if isinstance(effects, Failure):
        return _failed(effects)
    print("session  events  last event  state  unresolved effects")
    for info in infos:
        open_count = sum(1 for e in effects if e.action.session_id == info.session_id)
        print(
            f"{info.session_id}  {info.last_seq}  {info.last_at}"
            f"  {info.state}  {open_count}"
        )
    return 0


def _sizes(connection: sqlite3.Connection, folder: Path, blobs: FileBlobStore) -> int:
    report = store_sizes(connection, folder / "state.db")
    if isinstance(report, Failure):
        return _failed(report)
    totals = blobs.totals()
    if isinstance(totals, Failure):
        return _failed(totals)
    print(f"file: {report.file_bytes} bytes, write-ahead log: {report.log_bytes} bytes")
    print(
        f"pages: {report.pages} of {report.page_bytes} bytes,"
        f" {report.free_pages} free for reuse"
    )
    for table in report.tables:
        print(f"{table.table}: {table.rows} rows, {table.json_bytes} bytes of JSON")
    print(f"blobs: {totals.count}, {totals.total_bytes} bytes")
    print("largest sessions:")
    for session in report.largest_sessions:
        sizes = f"{session.events} events, {session.json_bytes} bytes"
        print(f"  {session.session_id}: {sizes}")
    return 0


async def _delete(
    connection: sqlite3.Connection,
    blobs: FileBlobStore,
    session_id: str,
    export_to: Path,
) -> int:
    report = await delete_session(
        connection, blobs, session_id, export_to, SystemClock()
    )
    if isinstance(report, Failure):
        return _failed(report)
    # The sweep is the one job that needs both the SQLite file and the blob
    # folder, so it is joined here and nowhere else.
    in_use = hashes_in_use(connection)
    if isinstance(in_use, Failure):
        return _failed(in_use)
    removed = blobs.remove_unused(in_use)
    if isinstance(removed, Failure):
        return _failed(removed)
    print(
        f"Deleted session {session_id} after exporting it to {report.export.folder}."
        f" Removed {removed} blobs that nothing else uses."
    )
    if not report.log_cleared:
        print("Another process was busy, so the next start clears the log file.")
    return 0


def _failed(failure: Failure) -> int:
    print(failure.message, file=sys.stderr)
    return 1
