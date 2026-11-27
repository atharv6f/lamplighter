"""The spans against a real Phoenix 22.1.0, started by the repo's script for each test.

These carry the `phoenix` marker, which the default run leaves out:

    uv run pytest -m phoenix

The first run installs Phoenix into scripts/phoenix/.venv, about 700 MB.
Each test starts a Phoenix of its own on a temp folder and two free ports,
so the owner's own Phoenix and its folder are never touched.
"""

import asyncio
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from lamplighter.adapters.in_memory import InMemoryTraceCursor
from lamplighter.adapters.phoenix import PhoenixSink
from lamplighter.adapters.system_clock import SystemClock
from lamplighter.app.main import main
from lamplighter.core import TraceProjector
from lamplighter.ports import Event, Failure, Span
from tests.kill_kit import ended, killed_once, new_world, points, run_process
from tests.kill_scenarios import ALL
from tests.phoenix_process import VERSION, free_port, launch, running_phoenix
from tests.phoenix_stand_in import stand_in
from tests.projector_kit import ListedLog
from tests.span_checks import log_spans
from tests.span_count import promised_spans
from tests.span_kit import MadeUpLog
from tests.test_trace_commands import runs_log, saved, settings

pytestmark = pytest.mark.phoenix

ROOT = Path(__file__).parents[1]
# Phoenix's own word for each kind of span.
PHOENIX_KIND = {"run": "AGENT", "step": "CHAIN", "model": "LLM", "tool": "TOOL"}
KILLS = 10


def drained(
    url: str, events: Sequence[Event], cursor: InMemoryTraceCursor
) -> list[object]:
    """Pass over `events` into the Phoenix at `url` until a pass is idle or fails."""

    async def passes() -> list[object]:
        async with httpx.AsyncClient(trust_env=False) as http:
            sink = PhoenixSink(url, "lamplighter", http, SystemClock())
            projector = TraceProjector(ListedLog(events), sink, cursor)
            done: list[object] = []
            while True:
                done.append(await projector.run_pass())
                last = done[-1]
                if isinstance(last, Failure) or getattr(last, "events", 0) == 0:
                    return done

    return asyncio.run(passes())


def ms(text: str) -> int:
    moment = datetime.fromisoformat(text)
    return int(moment.timestamp() * 1000 + 0.5)


def test_the_script_starts_the_pinned_version(tmp_path: Path) -> None:
    with running_phoenix(tmp_path / "phoenix") as phoenix:
        version = phoenix.get("/arize_phoenix_version").decode()

    assert version == VERSION


def test_a_kill_log_reads_back_from_phoenix_as_it_was_sent(tmp_path: Path) -> None:
    scenario = next(s for s in ALL if s.name.startswith("three steps with two tools"))
    world = new_world(scenario)
    calls = run_process(world, scenario).calls
    events = ended(killed_once(scenario, points(calls)[40])).events
    truth = {made.span.span_id: made.span for made in log_spans(events)}

    with running_phoenix(tmp_path / "phoenix") as phoenix:
        assert drained(phoenix.url, events, InMemoryTraceCursor())[-1] is not None
        rows = phoenix.spans("lamplighter")

    assert len(truth) == promised_spans(events) >= 10
    assert {row["context"]["span_id"] for row in rows} == truth.keys()
    for row in rows:
        sent: Span = truth[row["context"]["span_id"]]
        attributes = row["attributes"]
        assert row["context"]["trace_id"] == sent.trace_id
        assert row["parent_id"] == sent.parent_id
        assert row["name"] == sent.name
        assert ms(row["start_time"]) == sent.start_ms
        assert ms(row["end_time"]) == sent.start_ms + sent.duration_ms
        assert row["span_kind"] == PHOENIX_KIND[sent.kind]
        assert attributes["lamp.config_id"] == sent.attributes["lamp.config_id"]
        assert attributes["lamp.run_id"] == sent.attributes["lamp.run_id"]
        # Phoenix files a span under a session by the conversation id it holds.
        session = sent.attributes["gen_ai.conversation.id"]
        assert (
            attributes["gen_ai.conversation.id"] == attributes["session.id"] == session
        )
        assert (row["status_code"] == "ERROR") == (sent.error is not None)


def one_run(run_id: str) -> list[Event]:
    log = MadeUpLog()
    run = log.run("sess-a", run_id)
    run.start()
    run.answered()
    run.end()
    return log.events


def test_a_session_used_again_after_a_delete_gives_a_second_trace(
    tmp_path: Path,
) -> None:
    # After the delete the new run's events have the same ids as the old
    # run's, so only the run id in a span's id keeps the two apart.
    with running_phoenix(tmp_path / "phoenix") as phoenix:
        path = settings(tmp_path, phoenix.url)
        saved(tmp_path, one_run("run-1"))
        assert main(["--config", path, "traces", "export"]) == 0
        exported = str(tmp_path / "exported")
        assert (
            main(
                ["--config", path, "store", "delete", "sess-a", "--export-to", exported]
            )
            == 0
        )
        saved(tmp_path, one_run("run-2"))
        assert main(["--config", path, "traces", "export"]) == 0
        rows = phoenix.spans("lamplighter")

    assert len(rows) == 6
    assert len({row["context"]["trace_id"] for row in rows}) == 2
    assert {row["attributes"]["lamp.run_id"] for row in rows} == {"run-1", "run-2"}


def test_an_export_after_a_reset_keeps_the_same_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    events = runs_log(60)
    saved(tmp_path, events)
    with running_phoenix(tmp_path / "phoenix") as phoenix:
        path = settings(tmp_path, phoenix.url)
        assert main(["--config", path, "traces", "export"]) == 0
        first = len(phoenix.spans("lamplighter"))
        assert main(["--config", path, "traces", "reset"]) == 0
        assert main(["--config", path, "traces", "export"]) == 0
        second = len(phoenix.spans("lamplighter"))

    assert first == second == promised_spans(events)
    sent = [line for line in capsys.readouterr().out.splitlines() if "Sent" in line]
    assert len(sent) == 2 and sent[0] == sent[1]


def test_a_phoenix_that_is_not_running_leaves_the_cursor_and_then_gets_all(
    tmp_path: Path,
) -> None:
    events = runs_log(20)
    port, cursor = free_port(), InMemoryTraceCursor()
    url = f"http://127.0.0.1:{port}"

    before = drained(url, events, cursor)
    assert len(before) == 1 and isinstance(before[0], Failure)
    assert before[0].kind == "unavailable"
    assert asyncio.run(cursor.read()) == asyncio.run(InMemoryTraceCursor().read())

    phoenix = launch(tmp_path / "phoenix", port, free_port())
    try:
        drained(url, events, cursor)
        held = set(phoenix.names())
    finally:
        phoenix.stop()
    assert held == {made.span.span_id for made in log_spans(events)}


def test_phoenix_killed_at_ten_moments_of_an_export_loses_no_span(
    tmp_path: Path,
) -> None:
    events = runs_log(200)
    folder, port, grpc = tmp_path / "phoenix", free_port(), free_port()
    url, cursor, chance = (
        f"http://127.0.0.1:{port}",
        InMemoryTraceCursor(),
        random.Random(7),
    )
    failures: list[object] = []
    for _ in range(KILLS):
        phoenix = launch(folder, port, grpc)
        killer = threading.Timer(chance.uniform(0.3, 3.0), phoenix.stop)
        killer.start()
        try:
            failures += [
                d for d in drained(url, events, cursor) if isinstance(d, Failure)
            ]
        finally:
            killer.join()
            phoenix.stop()
    phoenix = launch(folder, port, grpc)
    try:
        last = drained(url, events, cursor)
        held = set(phoenix.names())
    finally:
        phoenix.stop()

    assert not isinstance(last[-1], Failure), last[-1]
    assert len(failures) >= KILLS // 2, "the kills landed after the exports"
    assert len(held) == promised_spans(events)
    assert held == {made.span.span_id for made in log_spans(events)}


def test_spans_already_stored_under_another_project_end_in_a_timeout(
    tmp_path: Path,
) -> None:
    spans = [made.span for made in log_spans(one_run("run-1"))]

    async def export(url: str, project: str) -> Failure | None:
        async with httpx.AsyncClient(trust_env=False) as http:
            sink = PhoenixSink(url, project, http, SystemClock())
            return await sink.export(spans)

    with running_phoenix(tmp_path / "phoenix") as phoenix:
        assert asyncio.run(export(phoenix.url, "first")) is None
        began = time.monotonic()
        failed = asyncio.run(export(phoenix.url, "second"))
        took = time.monotonic() - began

    assert isinstance(failed, Failure)
    assert (failed.kind, failed.effect) == ("timeout", "unknown")
    assert failed.message.startswith(
        f"The trace store took the batch but does not show {len(spans)} of its"
        " spans in project second."
    )
    assert 30 <= took < 45


def outside_address() -> str | None:
    """Give this machine's address on its network, or None when it has none."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # A datagram socket sends nothing when it connects.
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    return None if address.startswith("127.") else address


def test_a_hostile_environment_changes_nothing_phoenix_does(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with stand_in() as listener:
        env_file = tmp_path / "more.env"
        env_file.write_text(
            f"PHOENIX_SQL_DATABASE_URL=sqlite:///{other}/second.db\n"
            "PHOENIX_SERVER_INSTRUMENTATION_OTLP_TRACE_COLLECTOR_HTTP_ENDPOINT="
            f"{listener.url}/v1/traces\n"
        )
        hostile = {
            "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(Path.home()),
            "UV_ENV_FILE": str(env_file),
            "PHOENIX_SQL_DATABASE_URL": f"sqlite:///{other}/first.db",
            "PHOENIX_SERVER_INSTRUMENTATION_OTLP_TRACE_COLLECTOR_HTTP_ENDPOINT": (
                f"{listener.url}/v1/traces"
            ),
            "OTEL_EXPORTER_OTLP_ENDPOINT": listener.url,
            "PHOENIX_HOST": "0.0.0.0",
            "ANTHROPIC_API_KEY": "made-up-key-0000",
        }
        hostile = _with_uv(hostile)
        with running_phoenix(tmp_path / "phoenix", env=hostile) as phoenix:
            spans = [made.span for made in log_spans(one_run("run-1"))][:5]
            asyncio.run(_send(phoenix.url, spans))
            # Phoenix sends traces of its own work a few seconds after it.
            time.sleep(6)
            outside = outside_address()
            ports = (phoenix.port, phoenix.grpc_port)
            reachable = [p for p in ports if outside and _answers(outside, p)]
            reached = len(listener.asked)

    assert outside is not None, "no address off loopback to try"
    assert reachable == []
    assert any((tmp_path / "phoenix").glob("*.db"))
    assert list(other.iterdir()) == []
    assert reached == 0


def _with_uv(env: dict[str, str]) -> dict[str, str]:
    """Put the folder of the `uv` that runs these tests on PATH."""
    found = shutil.which("uv")
    assert found is not None
    return env | {"PATH": f"{Path(found).parent}:{env['PATH']}"}


async def _send(url: str, spans: Sequence[Span]) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        sink = PhoenixSink(url, "lamplighter", http, SystemClock())
        assert await sink.export(spans) is None


def _answers(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=2):
            return True
    except OSError:
        return False


def test_the_count_command_compares_the_log_with_what_phoenix_lists(
    tmp_path: Path,
) -> None:
    events = runs_log(30)
    saved(tmp_path, events)
    with running_phoenix(tmp_path / "phoenix") as phoenix:
        path = settings(tmp_path, phoenix.url)
        before = _count(path)
        assert main(["--config", path, "traces", "export"]) == 0
        after = _count(path)

    promised = promised_spans(events)
    assert before == (
        f"The log promises {promised} spans. Phoenix lists 0 in project lamplighter.\n"
    )
    assert after == (
        f"The log promises {promised} spans. Phoenix lists {promised} in project"
        " lamplighter.\n"
    )


def _count(path: str) -> str:
    return subprocess.run(
        [sys.executable, "-m", "tests.span_count", "--config", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
