"""Entry point: builds the adapters and runs a command."""

import argparse
import asyncio
import dataclasses
import functools
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

import httpx

from lamplighter.adapters import (
    FakeExecutor,
    FileBlobStore,
    FileLedger,
    FileTraceCursor,
    SystemClock,
    SystemIds,
    TerminalChannel,
)
from lamplighter.adapters.anthropic import AnthropicModel
from lamplighter.adapters.builtin_tools import (
    BuiltinTools,
    ToolSettings,
    closed_web,
    system_web,
)
from lamplighter.adapters.container import AsRoot, ContainerExecutor
from lamplighter.adapters.harbor_jobs import HarborJobs, HarborPaths
from lamplighter.adapters.in_memory import InMemoryBlobStore
from lamplighter.adapters.phoenix import PhoenixSink, local_address
from lamplighter.adapters.seatbelt import SeatbeltExecutor
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    delete_session,
    export_session,
    hashes_in_use,
    open_database,
    store_sizes,
)
from lamplighter.app.bench_commands import add_bench_parts, bench_command
from lamplighter.app.bench_parts import BenchParts
from lamplighter.app.bench_settings import (
    BenchSettings,
    harbor_version,
    load_bench_settings,
)
from lamplighter.app.bench_trial import (
    TrialModel,
    TrialParts,
    TrialRequest,
    TrialStore,
    run_trial,
)
from lamplighter.app.chat import OFF_THIS_MAC, ChatParts, Stores, Tooling, chat
from lamplighter.app.code_revision import REPO_ROOT, code_revision
from lamplighter.app.config import Config, load_config
from lamplighter.app.report import day_lines
from lamplighter.app.traces import export_spans, reset_cursor
from lamplighter.bench import TrialRecord
from lamplighter.core import SPAN_LAYOUT, portable_config_id
from lamplighter.ports import (
    BlobStore,
    Clock,
    Command,
    Executor,
    Failure,
    ModelClient,
    ToolEntry,
    ToolProvider,
    TraceCursor,
    TraceSink,
    canonical_json,
)

NO_KEY = "ANTHROPIC_API_KEY is not set."
BENCH = REPO_ROOT / "bench"
# How long a write waits for another process that holds the store's lock.
STORE_TIMEOUT_S = 5.0
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def build_model(config: Config, api_key: str) -> ModelClient:
    """Build the client for the model the settings name."""
    return AnthropicModel(config.model, api_key, httpx.AsyncClient(), SystemClock())


def trace_sink(config: Config, clock: Clock) -> PhoenixSink:
    """Build the sink for the trace store the settings name.

    The client takes no proxy from the environment, so spans for an
    address on this Mac can never be routed through another machine.
    """
    http = httpx.AsyncClient(trust_env=False, follow_redirects=False)
    traces = config.traces
    return PhoenixSink(traces.phoenix_url, traces.project, http, clock)


def trace_cursor(config: Config) -> FileTraceCursor:
    """Build the cursor of the trace store, project and span layout in the settings.

    The target holds the address the loopback rule gives back, or none.
    The setting itself never goes in: one that fails the rule may hold any
    text. With no address, every command refuses the sink first, so that
    cursor is never read or written.
    """
    address = local_address(config.traces.phoenix_url) or ""
    target = canonical_json([address, config.traces.project, SPAN_LAYOUT])
    return FileTraceCursor(Path(config.store.dir), target)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command on the command line, and give the exit code."""
    args = _parser().parse_args(argv)
    if args.command is None:
        print("lamplighter: nothing to run yet")
        return 0
    if args.command == "bench":
        # The settings file is the bench's to load: a plan refuses one that
        # does not load, with its own line.
        return _bench(args)
    config = load_config(args.config)
    if args.command == "chat":
        return _chat(config, args.session)
    if args.command == "traces":
        # Refused before the cursor is touched, so none is ever kept for an
        # address off this Mac.
        if trace_sink(config, SystemClock()).leaves_this_mac:
            print(OFF_THIS_MAC, file=sys.stderr)
            return 1
        if args.action == "reset":
            cursor = trace_cursor(config)
            return asyncio.run(reset_cursor(cursor, sys.stdout, sys.stderr))
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
    talk = commands.add_parser("chat", help="talk to one session in the terminal")
    talk.add_argument("--session", help="carry on this session, or start it")
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
    traces = commands.add_parser("traces", help="send spans to the trace store")
    steps = traces.add_subparsers(dest="action", required=True)
    steps.add_parser("export", help="send every span not yet sent")
    steps.add_parser("reset", help="send every span again at the next export")
    report = commands.add_parser("report", help="report one day from the log")
    report.add_argument("--day", type=_day, help="the day in UTC, as YYYY-MM-DD")
    add_bench_parts(commands.add_parser("bench", help="run the bench and the gate"))
    return parser


def _day(text: str) -> str:
    try:
        if _DAY.fullmatch(text):
            return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"not a day as YYYY-MM-DD: {text!r}")


def _chat(config: Config, session_id: str | None) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print(NO_KEY, file=sys.stderr)
        return 1
    model = build_model(config, key)
    clock = SystemClock()
    tooling = builtin_tooling(config, clock)
    parts = chat_parts(
        config,
        model,
        tooling,
        trace_sink(config, clock),
        trace_cursor(config),
        sys.stdin.fileno(),
        sys.stdout,
        sys.stderr,
        code_revision(REPO_ROOT),
    )
    return chat(config, session_id, parts)


def builtin_tooling(config: Config, clock: Clock) -> Callable[[BlobStore], Tooling]:
    """Build the built-in tools behind the wall, once their folders are made."""

    def build(blobs: BlobStore) -> Tooling:
        tools = config.tools
        executor = SeatbeltExecutor(
            tools.work_dir, tools.scratch_dir, config.store.dir, clock
        )
        built = BuiltinTools(tools, blobs, executor, clock, system_web())
        return Tooling(built, executor)

    return build


def tool_entries(config: Config) -> tuple[ToolEntry, ...]:
    """List the built-in tools the settings give, without a wall or a store.

    The tools are built on a fake executor, an empty folder and blobs in
    memory, only to be listed: the list does not depend on any of them.
    """
    with tempfile.TemporaryDirectory() as folder:
        settings = dataclasses.replace(config.tools, work_dir=folder)
        blobs = InMemoryBlobStore(config.store.max_blob_bytes)
        executor = FakeExecutor(Path(folder), _no_command)
        built = BuiltinTools(settings, blobs, executor, SystemClock(), closed_web())
        return built.list_tools()


def _no_command(command: Command) -> Failure:
    return Failure("fatal", "none", "Only listed, never run.", "tool_entries")


def chat_parts(
    config: Config,
    model: ModelClient,
    tooling: Callable[[BlobStore], Tooling],
    sink: TraceSink,
    cursor: TraceCursor,
    reader: int,
    writer: TextIO,
    errors: TextIO,
    revision: str,
) -> ChatParts:
    """Build a chat on the SQLite store and a terminal that reads `reader`.

    The chat sends the spans of its log to `sink`, and keeps how far it got
    in `cursor`. Every run saves `revision` as the code that made it.
    """
    clock = SystemClock()
    ids = SystemIds()

    def open_stores(folder: Path) -> Stores | Failure:
        return sqlite_stores(folder, config, clock)

    def terminal(session_id: str) -> TerminalChannel:
        return TerminalChannel(session_id, clock, ids, reader, writer)

    return ChatParts(
        model,
        tooling,
        clock,
        ids,
        open_stores,
        terminal,
        writer,
        errors,
        sink,
        cursor,
        revision,
    )


def sqlite_stores(folder: Path, config: Config, clock: Clock) -> Stores | Failure:
    """Open the store file and the blob folder in `folder`, and build the stores."""
    connection = open_database(folder / "state.db", STORE_TIMEOUT_S)
    if isinstance(connection, Failure):
        return connection
    return stores_on(connection, folder, config, clock)


def stores_on(
    connection: sqlite3.Connection, folder: Path, config: Config, clock: Clock
) -> Stores:
    """Build the stores on an open store file, with the blob folder in `folder`.

    Closing the stores closes the connection.
    """
    max_record_bytes = config.store.max_record_bytes
    return Stores(
        log=SqliteEventLog(connection, {}, max_record_bytes),
        inbox=SqliteInbox(connection, max_record_bytes),
        outbox=SqliteOutbox(connection, clock, max_record_bytes),
        ledger=SqliteEffectLedger(connection, clock, max_record_bytes),
        blobs=FileBlobStore(folder / "blobs", clock, config.store.max_blob_bytes),
        close=connection.close,
    )


def trial_store(folder: Path, config: Config, clock: Clock) -> TrialStore | Failure:
    """Open a new store for one trial in `folder`, and say how to export its session.

    The trial holds the connection, because the export reads it.
    """
    folder.mkdir(mode=0o700)
    connection = open_database(folder / "state.db", STORE_TIMEOUT_S)
    if isinstance(connection, Failure):
        return connection
    stores = stores_on(connection, folder, config, clock)

    async def export(session_id: str, into: Path) -> int | Failure:
        made = await export_session(connection, stores.blobs, session_id, into, clock)
        return made if isinstance(made, Failure) else made.events

    return TrialStore(stores, export)


def trial_parts(api_key: str, revision: str) -> TrialParts:
    """Build the parts of a trial on the real stores, the container and the model.

    Web access is closed in a trial: every fetch gets the tool's own result
    for a name that cannot be found.
    """
    clock = SystemClock()

    def executor(
        as_root: AsRoot, user: tuple[int, int], work: Path
    ) -> ContainerExecutor:
        return ContainerExecutor(as_root, user[0], user[1], work, SystemIds())

    def tools(settings: ToolSettings, blobs: BlobStore, wall: Executor) -> ToolProvider:
        return BuiltinTools(settings, blobs, wall, clock, closed_web())

    def model(config: Config) -> TrialModel:
        http = httpx.AsyncClient()
        client = AnthropicModel(config.model, api_key, http, clock)
        return TrialModel(client, http.aclose)

    return TrialParts(
        clock=clock,
        ids=SystemIds(),
        code_revision=revision,
        open_store=lambda folder, config: trial_store(folder, config, clock),
        executor=executor,
        tools=tools,
        model=model,
    )


def _bench(args: argparse.Namespace) -> int:
    settings = load_bench_settings(BENCH / "bench.toml", Path.home())
    if isinstance(settings, Failure):
        return _failed(settings)
    version = harbor_version(BENCH / "harbor" / "uv.lock")
    if isinstance(version, Failure):
        return _failed(version)
    return asyncio.run(bench_command(bench_parts(settings, version), args, args.config))


def bench_parts(settings: BenchSettings, version: str) -> BenchParts:
    """Build the bench command on Harbor, the ledger file, this checkout and terminal.

    The bench folder is made here, so that the ledger and the job folders
    have somewhere to go.
    """
    folder = Path(settings.dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return BenchParts(
        jobs=harbor_jobs(folder),
        check_jobs=harbor_jobs(folder / "checks"),
        ledger=FileLedger(folder / "ledger.jsonl"),
        clock=SystemClock(),
        ids=SystemIds(),
        root=REPO_ROOT,
        settings=settings,
        harbor_version=version,
        portable_id=settings_portable_id,
        environ=os.environ,
        user=(os.getuid(), os.getgid()),
        out=sys.stdout,
        errors=sys.stderr,
    )


def harbor_jobs(bench: Path) -> HarborJobs:
    """Build the job runner on the pinned Harbor, with its job folders in `bench`."""
    harbor = BENCH / "harbor"
    return HarborJobs(
        HarborPaths(
            start_script=harbor / "start.sh",
            bench=bench,
            tasks=BENCH / "tasks",
            compose=harbor / "no_network.yaml",
            agent_class="harbor_shim.agent:HarnessAgent",
            environment_class="harbor_shim.environment:SharedFolderDocker",
        )
    )


def settings_portable_id(config: Config) -> str:
    """Give the portable config id of a settings file, without a trial."""
    return portable_config_id(
        config.model,
        config.prices,
        config.run,
        tool_entries(config),
        dataclasses.asdict(config.tools),
    )


async def bench_trial(request: TrialRequest) -> TrialRecord:
    """Run one trial of the bench on the real parts. Harbor's agent class calls it.

    The key is read from `ANTHROPIC_API_KEY`, as `chat` reads it.
    """
    parts = trial_parts(os.environ.get("ANTHROPIC_API_KEY", ""), _loaded_revision())
    return await run_trial(parts, request)


@functools.cache
def _loaded_revision() -> str:
    # Worked out at the first trial and kept: it names the code this process
    # loaded, which a later change to the checkout does not change.
    return code_revision(REPO_ROOT)


async def _store_command(
    args: argparse.Namespace, config: Config, connection: sqlite3.Connection
) -> int:
    clock = SystemClock()
    log = SqliteEventLog(connection, {}, config.store.max_record_bytes)
    if args.command == "traces":
        sink, cursor = trace_sink(config, clock), trace_cursor(config)
        return await export_spans(log, sink, cursor, clock, sys.stdout, sys.stderr)
    if args.command == "report":
        return await _report(log, args.day or clock.now().date().isoformat())
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


async def _report(log: SqliteEventLog, day: str) -> int:
    lines = await day_lines(log, day)
    if isinstance(lines, Failure):
        return _failed(lines)
    for line in lines:
        print(line)
    return 0


def _failed(failure: Failure) -> int:
    print(failure.message, file=sys.stderr)
    return 1
