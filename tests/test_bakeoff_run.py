"""The bake-off's `run` on a made-up capture set, model, server and readings."""

import asyncio
import dataclasses
import json
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest

from lamplighter.app.bakeoff import write_rule
from lamplighter.app.bakeoff_measure import read_notes, run
from lamplighter.bench import (
    HARD,
    BakeoffChoice,
    Candidate,
    CandidateMeasures,
    bakeoff_choice,
)
from lamplighter.ports import BakeoffBody, Failure, from_plain
from tests.bakeoff_kit import ASKED, GB, NOTES, Bakeoff, Readings, made_up_server
from tests.ladder_kit import DOWN
from tests.settings_text import with_local


def measured(bakeoff: Bakeoff) -> int:
    return asyncio.run(run(bakeoff.measure(), bakeoff.settings, "local", bakeoff.notes))


def candidate_rows(bakeoff: Bakeoff) -> list[BakeoffBody]:
    return [s for s in bakeoff.steps() if s.stage == "candidate"]


def test_a_measured_candidate_s_row_holds_every_criterion(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    digest = bakeoff.start()
    assert measured(bakeoff) == 0, bakeoff.said()
    [rule, row] = bakeoff.steps()
    assert (row.stage, row.rule_hash, row.capture_set_hash) == (
        "candidate",
        rule.rule_hash,
        digest,
    )
    assert (row.server, row.server_version, row.model, row.weights_sha256) == (
        "llama.cpp",
        "llama.cpp b12826",
        "local",
        "0" * 64,
    )
    assert (row.weights_source, row.quant) == (
        "https://example.invalid/made-up-weights",
        "Q4_K_M",
    )
    # In the rule's order, whatever order the notes gave them in.
    assert tuple(v.name for v in row.hard) == HARD
    assert all(v.passed for v in row.hard)
    measures = from_plain(CandidateMeasures, row.criteria)
    usable = measures.usable_replies
    assert usable.each == tuple(reason for _, _, reason in ASKED)
    assert (usable.usable, usable.total, usable.calls, usable.good_calls) == (
        2,
        5,
        2,
        1,
    )
    # The peak of the made-up readings, and the memory the notes add.
    assert measures.peak_memory.footprint_bytes == 22 * GB
    assert measures.peak_memory.added_bytes == 1_000_000
    assert measures.peak_memory.pressure_level == 2
    assert measures.peak_memory.reads >= 3
    assert len(measures.first_token_ms.each_ms) == 5
    assert measures.forced_schema.passed
    assert (measures.adapter_per_request, measures.adapter_evidence) == (
        "at_start",
        "The made-up docs say adapters load at start.",
    )
    assert bakeoff.ports == [9931]
    assert bakeoff.closed == 1
    assert "2 of 5 replies usable" in bakeoff.out()


def test_the_captured_requests_go_to_the_model_once_each_in_order(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    assert measured(bakeoff) == 0, bakeoff.said()
    texts = [r.messages[-1].parts[0] for r in bakeoff.model.requests]
    assert [getattr(t, "text", None) for t in texts] == [t for t, _, _ in ASKED]


def test_the_rule_reads_the_row_and_puts_the_candidate_up_to_soak(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    assert measured(bakeoff) == 0, bakeoff.said()
    choice = bakeoff_choice(bakeoff.bench.rows())
    assert isinstance(choice, BakeoffChoice), choice
    local = Candidate("llama.cpp", "local", "0" * 64)
    assert (choice.order, choice.out, choice.to_soak) == ((local,), (), local)


def test_the_server_s_own_timings_are_kept_and_the_speeds_are_measured(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    assert measured(bakeoff) == 0, bakeoff.said()
    [row] = candidate_rows(bakeoff)
    speeds = from_plain(CandidateMeasures, row.criteria).tokens_per_second
    assert set(speeds.server_timings) == {"8k", "32k"}


def nothing_written(bakeoff: Bakeoff) -> None:
    assert measured(bakeoff) == 1
    assert candidate_rows(bakeoff) == []


def test_with_no_rule_row_nothing_is_measured(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.write_capture()
    nothing_written(bakeoff)
    assert "no bake-off rule row" in bakeoff.said()
    assert bakeoff.model.requests == []


def test_an_entry_the_settings_do_not_name_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    code = asyncio.run(
        run(bakeoff.measure(), bakeoff.settings, "missing", bakeoff.notes)
    )
    assert code == 1
    assert "has no model entry named missing" in bakeoff.said()


def test_a_model_off_this_mac_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    code = asyncio.run(
        run(bakeoff.measure(), bakeoff.settings, "frontier", bakeoff.notes)
    )
    assert code == 1
    assert "frontier does not stay on this Mac" in bakeoff.said()
    assert bakeoff.model.requests == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "window = 65_536\nmax_output = 16_000",
            "window = 32_768\nmax_output = 16_000",
        ),
        ("window = 65_536\nmax_output = 16_000", "window = 65_536\nmax_output = 8_000"),
    ],
)
def test_an_entry_whose_sizes_are_not_the_rule_s_is_refused(
    tmp_path: Path, old: str, new: str
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    text = with_local()
    assert old in text
    bakeoff.settings.write_text(text.replace(old, new))
    nothing_written(bakeoff)
    assert "must have the rule's window, 65536, and its max_output" in bakeoff.said()


def test_a_capture_set_changed_since_the_rule_row_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    [path] = bakeoff.captures().glob("capture-*.jsonl")
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    nothing_written(bakeoff)
    assert "is not the capture set the rule row names" in bakeoff.said()
    path.unlink()
    nothing_written(bakeoff)
    assert "could not be read" in bakeoff.said()


def test_an_empty_capture_set_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.write_capture(())
    assert asyncio.run(write_rule(bakeoff.parts(), "", "")) == 0
    nothing_written(bakeoff)
    assert "holds no request" in bakeoff.said()


@pytest.mark.parametrize(
    ("notes", "said"),
    [
        (NOTES.replace('name = "pinned"', 'name = "loopback_only"'), "once"),
        (
            NOTES.replace('[[hard]]\nname = "pinned"', '[[made_up]]\nname = "pinned"'),
            "",
        ),
        (NOTES.replace("added_bytes = 1_000_000", "added_bytes = -1"), "below 0"),
        (NOTES.replace('"at_start"', '"sometimes"'), "could not be read"),
        (NOTES.replace("[[hard]]", "[[hard", 1), "could not be read"),
        (NOTES + 'colour = "made-up"\n', "could not be read"),
    ],
)
def test_notes_that_do_not_hold_what_the_row_needs_are_refused(
    tmp_path: Path, notes: str, said: str
) -> None:
    path = tmp_path / "notes.toml"
    path.write_text(notes)
    found = read_notes(path)
    assert isinstance(found, Failure)
    assert said in found.message


def test_notes_missing_one_hard_requirement_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.toml"
    cut = NOTES.index('[[hard]]\nname = "pinned"')
    path.write_text(NOTES[:cut] + NOTES[NOTES.index("[[hard]]", cut + 1) :])
    found = read_notes(path)
    assert isinstance(found, Failure)
    assert "must give each of broken_call_visible" in found.message
    bakeoff = Bakeoff(tmp_path / "bench")
    bakeoff.start()
    bakeoff.notes.write_text(path.read_text())
    nothing_written(bakeoff)
    assert bakeoff.model.requests == []


def test_missing_notes_are_refused(tmp_path: Path) -> None:
    found = read_notes(tmp_path / "missing.toml")
    assert isinstance(found, Failure)
    assert "could not be read" in found.message


def test_a_server_that_is_not_up_is_refused_and_its_clients_closed(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.health.answer = DOWN
    nothing_written(bakeoff)
    assert "The server of local is not up: Refused." in bakeoff.said()
    assert bakeoff.closed == 1
    assert bakeoff.model.requests == []


def test_a_server_nobody_listens_for_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.listening = dataclasses.replace(DOWN, message="Nothing listens.")
    nothing_written(bakeoff)
    assert "Nothing listens." in bakeoff.said()
    assert bakeoff.closed == 1


def test_a_footprint_that_could_not_be_read_once_writes_nothing(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.footprint = Readings(20 * GB, None, 21 * GB)
    nothing_written(bakeoff)
    assert "could not be read 1 times of" in bakeoff.said()


def test_a_watch_that_breaks_raises_and_is_not_hidden(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()

    def broken() -> int | None:
        raise RuntimeError("a made-up bug in the reader")

    bakeoff.footprint = broken
    with pytest.raises(RuntimeError, match="made-up bug"):
        measured(bakeoff)
    assert candidate_rows(bakeoff) == []
    assert bakeoff.closed == 1


def test_a_server_that_refuses_the_timed_requests_writes_nothing(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()

    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"made up")

    bakeoff.server = refuse
    nothing_written(bakeoff)
    assert "The server answered 500" in bakeoff.said()
    assert bakeoff.closed == 1


def test_a_forced_schema_that_breaks_is_kept_as_a_failed_verdict(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.server = made_up_server('{"street": "Made-up Lane"}')
    assert measured(bakeoff) == 0, bakeoff.said()
    [row] = candidate_rows(bakeoff)
    forced = from_plain(CandidateMeasures, row.criteria).forced_schema
    assert not forced.passed
    assert "The reply breaks it" in forced.evidence


def test_a_run_killed_part_way_writes_nothing(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.model.stop_at = 2
    with pytest.raises(KeyboardInterrupt):
        measured(bakeoff)
    assert candidate_rows(bakeoff) == []
    assert bakeoff.closed == 1


def failing_when(
    fails: Callable[[dict[str, Any]], bool],
) -> Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]:
    answer = made_up_server()

    async def handle(request: httpx.Request) -> httpx.Response:
        if fails(json.loads(request.content)):
            return httpx.Response(500, content=b"made up")
        return await answer(request)

    return handle


def asks_speed(body: dict[str, Any]) -> bool:
    return body.get("max_tokens") == 512


def forces_a_schema(body: dict[str, Any]) -> bool:
    return "response_format" in body


@pytest.mark.parametrize("fails", [asks_speed, forces_a_schema])
def test_a_server_that_fails_a_later_timed_request_writes_nothing(
    tmp_path: Path, fails: Callable[[dict[str, Any]], bool]
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.server = failing_when(fails)
    nothing_written(bakeoff)
    assert "The server answered 500" in bakeoff.said()


def test_the_candidate_is_measured_on_the_latest_rule_row_s_set(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    later = bakeoff.write_capture(ASKED[:2])
    [first_rule] = bakeoff.steps()
    # The newest summary is the one the rule row takes.
    os.utime(next(bakeoff.captures().glob(f"capture-{later[:12]}.json")), (9e9, 9e9))
    replaced = write_rule(bakeoff.parts(), first_rule.rule_hash, "A made-up reason.")
    assert asyncio.run(replaced) == 0, bakeoff.said()
    assert measured(bakeoff) == 0, bakeoff.said()
    [row] = candidate_rows(bakeoff)
    assert row.capture_set_hash == later
    assert len(bakeoff.model.requests) == 2


def test_the_footprint_is_of_the_server_on_the_entry_s_port(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.settings.write_text(with_local().replace(":9931", ":8123"))
    assert measured(bakeoff) == 0, bakeoff.said()
    assert bakeoff.ports == [8123]
