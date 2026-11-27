"""Every frozen store gives the spans its events promise, and raises nothing.

The stores were written by older code, so their events lack fields that
are newer than they are. One was made by hand and holds a tool result
under a step with no request, which the loop never writes.
"""

import sqlite3
from pathlib import Path

import pytest

from lamplighter.adapters.sqlite import SqliteEventLog
from lamplighter.ports import Event
from tests.span_checks import log_spans, spans_wrong
from tests.span_count import promised_spans
from tests.sqlite.helpers import opened, run

STORES = Path(__file__).parents[1] / "stores"
LIMIT = 4_194_304


def events_of(tmp_path: Path, name: str) -> tuple[Event, ...]:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript((STORES / name).read_text())
    built.close()
    events = run(SqliteEventLog(opened(path), {}, LIMIT).tail(0, 10_000))
    assert isinstance(events, tuple)
    return events


# The first store was made before a run saved the id of its settings.
NO_CONFIG = ["invoke_agent lamplighter lacks lamp.config_id"]


@pytest.mark.parametrize(
    ("name", "count", "wrong"),
    [
        ("version_1.sql", 1, NO_CONFIG),
        ("version_2.sql", 22, []),
        ("version_2_tools.sql", 27, []),
        ("version_2_tools_with_model.sql", 27, []),
        ("version_2_tools_with_revision.sql", 27, []),
    ],
)
def test_a_frozen_store_gives_the_spans_its_events_promise(
    tmp_path: Path, name: str, count: int, wrong: list[str]
) -> None:
    events = events_of(tmp_path, name)

    assert len(log_spans(events)) == promised_spans(events) == count
    assert spans_wrong(events) == wrong


def test_the_store_with_the_model_names_it_on_every_model_span(
    tmp_path: Path,
) -> None:
    models = [
        made.span
        for made in log_spans(events_of(tmp_path, "version_2_tools_with_model.sql"))
        if made.span.kind == "model"
    ]

    assert len(models) == 8
    assert {span.name for span in models} == {"chat made-up-1"}
