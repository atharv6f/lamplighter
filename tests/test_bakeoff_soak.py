"""The bake-off's `soak` on a made-up model and readings, whole, failed and killed."""

import asyncio
import os
from pathlib import Path

import pytest

from lamplighter.app.bakeoff import choose, write_rule
from lamplighter.app.bakeoff_measure import run
from lamplighter.app.bakeoff_soak import (
    SOAK_MINUTES,
    SoakSample,
    SoakStart,
    progress_path,
    read_progress,
    soak,
    soak_counts,
    soak_folder,
)
from lamplighter.app.single_instance import lock_store
from lamplighter.bench import SoakCounts, SoakMeasures, stays_up
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    TextPart,
    from_plain,
    saved_json,
    to_plain,
)
from tests.bakeoff_kit import ANSWER_SECONDS, ASKED, GB, Bakeoff, Readings
from tests.ladder_kit import DOWN

MINUTE_MS = 60_000


def sample(
    minute: int,
    took_ms: int = 2_000,
    failed: bool = False,
    footprint_bytes: int | None = 20 * GB,
) -> SoakSample:
    return SoakSample(minute * MINUTE_MS, took_ms, failed, footprint_bytes)


def a_day() -> list[SoakSample]:
    return [sample(m) for m in range(SOAK_MINUTES)]


# What the samples show.


def test_a_steady_day_passes() -> None:
    counts = soak_counts(a_day(), whole=True)
    assert counts == SoakCounts(True, 1_440, 0, 20 * GB, 20 * GB, 2_000, 2_000)
    assert stays_up(counts)


def test_the_first_hour_is_its_first_sixty_minutes_and_the_last_the_last_sixty() -> (
    None
):
    day = a_day()
    day[59] = sample(59, took_ms=1_000, footprint_bytes=21 * GB)
    day[60] = sample(60, took_ms=1_000, footprint_bytes=30 * GB)
    # Minute 1,379 is just outside the last hour. Were it in, the median
    # of the last hour would be 3,000.
    day[1_379] = sample(1_379, took_ms=9_000)
    for minute in range(1_380, 1_440):
        day[minute] = sample(minute, took_ms=1_000 if minute % 2 else 3_000)
    day[-1] = sample(1_439, took_ms=1_000, footprint_bytes=22 * GB)
    counts = soak_counts(day, whole=True)
    assert counts.first_hour_peak_bytes == 21 * GB
    assert counts.end_bytes == 22 * GB
    assert counts.first_hour_median_ms == 2_000
    assert counts.last_hour_median_ms == 1_000


def test_a_failed_request_or_an_unread_footprint_is_a_failed_minute() -> None:
    day = a_day()
    day[10] = sample(10, failed=True)
    day[20] = sample(20, footprint_bytes=None)
    counts = soak_counts(day, whole=True)
    assert counts.failed == 2
    assert not stays_up(counts)


def test_the_end_is_the_last_footprint_read() -> None:
    day = a_day()
    day[-2] = sample(1_438, footprint_bytes=21 * GB)
    day[-1] = sample(1_439, footprint_bytes=None)
    assert soak_counts(day, whole=True).end_bytes == 21 * GB


def test_no_samples_count_nothing_and_do_not_pass() -> None:
    counts = soak_counts([], whole=False)
    assert counts == SoakCounts(False, 0, 0, 0, 0, 0, 0)
    assert not stays_up(counts)


# The command.


def soaked(bakeoff: Bakeoff) -> int:
    return asyncio.run(soak(bakeoff.measure(), bakeoff.settings, "local"))


def measured_bakeoff(tmp_path: Path) -> Bakeoff:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    code = asyncio.run(run(bakeoff.measure(), bakeoff.settings, "local", bakeoff.notes))
    assert code == 0, bakeoff.said()
    bakeoff.footprint = Readings(20 * GB)
    bakeoff.model.requests.clear()
    return bakeoff


def soaks(bakeoff: Bakeoff) -> list[tuple[BakeoffBody, SoakMeasures]]:
    return [
        (s, from_plain(SoakMeasures, s.criteria["stays_up"]))
        for s in bakeoff.steps()
        if s.stage == "soak"
    ]


def test_a_whole_soak_asks_once_a_minute_in_turn_and_passes(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    assert soaked(bakeoff) == 0, bakeoff.said()
    [(row, measures)] = soaks(bakeoff)
    assert measures == SoakMeasures(
        True, SoakCounts(True, 1_440, 0, 20 * GB, 20 * GB, 2_000, 2_000)
    )
    [rule, candidate, _] = bakeoff.steps()
    assert (row.rule_hash, row.capture_set_hash) == (
        rule.rule_hash,
        rule.capture_set_hash,
    )
    assert (row.server, row.server_version, row.model, row.weights_sha256) == (
        "llama.cpp",
        "llama.cpp b12826",
        "local",
        "0" * 64,
    )
    assert (row.weights_source, row.quant) == (
        candidate.weights_source,
        candidate.quant,
    )
    texts = [r.messages[-1].parts[0] for r in bakeoff.model.requests]
    assert all(isinstance(t, TextPart) for t in texts)
    asked = [t.text for t in texts if isinstance(t, TextPart)]
    assert asked == [ASKED[m % len(ASKED)][0] for m in range(SOAK_MINUTES)]
    # Each minute waits what is left of it after the request, which the
    # made-up model takes its time on.
    left = 60 - ANSWER_SECONDS
    assert bakeoff.clock.slept[-4:] == [left, ANSWER_SECONDS, left, ANSWER_SECONDS]
    assert bakeoff.awake == ["start", "stop"]
    assert not progress_path(bakeoff.parts()).exists()
    assert "passed" in bakeoff.out()


def test_the_soak_that_passed_is_the_one_chosen(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    assert asyncio.run(choose(bakeoff.parts())) == 1
    assert "Soak llama.cpp local first" in bakeoff.said()
    assert soaked(bakeoff) == 0, bakeoff.said()
    assert asyncio.run(choose(bakeoff.parts())) == 0, bakeoff.said()
    choice = bakeoff.steps()[-1]
    assert (choice.stage, choice.chosen, choice.model) == ("choice", True, "local")


def test_a_server_that_grows_does_not_pass(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    bakeoff.footprint = Readings(*([20 * GB] * 60), 23 * GB)
    assert soaked(bakeoff) == 0, bakeoff.said()
    [(_, measures)] = soaks(bakeoff)
    assert (measures.counts.first_hour_peak_bytes, measures.counts.end_bytes) == (
        20 * GB,
        23 * GB,
    )
    assert not measures.passed
    assert "did not pass" in bakeoff.out()


def test_a_request_that_fails_fails_the_soak(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    bakeoff.model.fail_from = 1_000
    assert soaked(bakeoff) == 0, bakeoff.said()
    [(_, measures)] = soaks(bakeoff)
    assert measures.counts.failed == 440
    assert not measures.passed


def killed(bakeoff: Bakeoff, after: int) -> None:
    bakeoff.model.stop_at = after
    with pytest.raises(KeyboardInterrupt):
        soaked(bakeoff)
    bakeoff.model.stop_at = None


def test_a_soak_killed_part_way_is_written_by_the_next_as_not_whole(
    tmp_path: Path,
) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    killed(bakeoff, 10)
    assert bakeoff.awake == ["start", "stop"]
    assert soaks(bakeoff) == []
    assert len(progress_path(bakeoff.parts()).read_text().splitlines()) == 11
    assert soaked(bakeoff) == 0, bakeoff.said()
    [(cut, cut_measures), (_, whole)] = soaks(bakeoff)
    assert cut_measures.counts.whole is False
    assert cut_measures.counts.requests == 10
    assert not cut_measures.passed
    assert whole.passed
    assert cut.server == "llama.cpp"
    assert "cut short after 10 minutes" in bakeoff.out()


def test_a_line_cut_by_the_kill_is_left_out(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    killed(bakeoff, 4)
    path = progress_path(bakeoff.parts())
    with path.open("a") as file:
        file.write('{"at_ms": 24')
    found = read_progress(path.read_text())
    assert found is not None
    assert len(found[1]) == 4
    assert soaked(bakeoff) == 0, bakeoff.said()
    assert soaks(bakeoff)[0][1].counts.requests == 4


def test_a_file_with_not_even_its_first_line_whole_writes_no_row() -> None:
    assert read_progress("") is None
    assert read_progress('{"row_id": "made') is None


def test_a_file_whose_row_is_already_in_is_only_removed(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    assert soaked(bakeoff) == 0, bakeoff.said()
    [row] = [
        r
        for r in bakeoff.bench.rows()
        if isinstance(r, BakeoffRow) and r.body.stage == "soak"
    ]
    # As if the soak was killed after its row went in and before the file went.
    path = progress_path(bakeoff.parts())
    start = SoakStart(row.row_id, row.body)
    path.write_text(saved_json(to_plain(start)) + "\n")
    assert soaked(bakeoff) == 0, bakeoff.said()
    assert len(soaks(bakeoff)) == 2
    assert all(m.passed for _, m in soaks(bakeoff))
    assert "cut short" not in bakeoff.out()


def test_a_file_under_an_earlier_rule_row_is_only_removed(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    killed(bakeoff, 3)
    rule = bakeoff.steps()[0]
    later = bakeoff.write_capture(ASKED[:2])
    newest = bakeoff.captures() / f"capture-{later[:12]}.json"
    os.utime(newest, (9e9, 9e9))
    replaced = write_rule(bakeoff.parts(), rule.rule_hash, "A made-up reason.")
    assert asyncio.run(replaced) == 0, bakeoff.said()
    assert soaked(bakeoff) == 1
    assert "has no candidate row under the rule row" in bakeoff.said()
    assert soaks(bakeoff) == []
    assert not progress_path(bakeoff.parts()).exists()


def test_a_file_that_a_soak_did_not_write_is_refused_and_kept(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    path = progress_path(bakeoff.parts())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"made": "up"}\n')
    assert soaked(bakeoff) == 1
    assert "is not a soak's file" in bakeoff.said()
    assert path.exists()
    assert bakeoff.awake == []


def test_one_soak_runs_at_a_time(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    held = lock_store(soak_folder(bakeoff.parts()))
    assert held is not None
    try:
        assert soaked(bakeoff) == 1
    finally:
        os.close(held)
    assert "Another soak is running." in bakeoff.said()
    assert bakeoff.model.requests == []


def test_a_candidate_not_measured_under_the_rule_is_not_soaked(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    assert soaked(bakeoff) == 1
    assert "llama.cpp local has no candidate row under the rule row" in bakeoff.said()
    assert bakeoff.awake == []
    assert bakeoff.model.requests == []


def test_a_server_that_is_not_up_is_not_soaked(tmp_path: Path) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    bakeoff.health.answer = DOWN
    assert soaked(bakeoff) == 1
    assert "The server of local is not up" in bakeoff.said()
    assert bakeoff.awake == ["start", "stop"]
    assert not progress_path(bakeoff.parts()).exists()
    assert soaks(bakeoff) == []


def test_a_candidate_measured_again_is_soaked_as_its_latest_row_says(
    tmp_path: Path,
) -> None:
    bakeoff = measured_bakeoff(tmp_path)
    bakeoff.notes.write_text(bakeoff.notes.read_text().replace("Q4_K_M", "Q8_0"))
    bakeoff.footprint = Readings(20 * GB, 21 * GB, 20 * GB)
    code = asyncio.run(run(bakeoff.measure(), bakeoff.settings, "local", bakeoff.notes))
    assert code == 0, bakeoff.said()
    bakeoff.footprint = Readings(20 * GB)
    assert soaked(bakeoff) == 0, bakeoff.said()
    [(row, _)] = soaks(bakeoff)
    assert row.quant == "Q8_0"
