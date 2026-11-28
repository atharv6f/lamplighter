"""The bake-off's commands that keep to the ledger: capture, rule, choose, table."""

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from lamplighter.app.bakeoff import (
    capture,
    choose,
    latest_capture,
    table,
    table_lines,
    write_rule,
)
from lamplighter.app.bakeoff_command import add_bakeoff_parts, bakeoff_command
from lamplighter.app.bakeoff_measure import run
from lamplighter.app.capture_set import read_capture, set_hash
from lamplighter.bench import bakeoff_choice, bakeoff_rule_hash, read_bakeoff_rule
from lamplighter.ports import Failure, TextPart
from tests.bakeoff_kit import GB, Bakeoff, Readings, only_local
from tests.ladder_kit import DOWN
from tests.run_history import History
from tests.settings_text import LOCAL, with_local
from tests.test_capture_sources import MATCHED, OTHER, answered_run, saved

RULE_HASH = "e87050e27fcab5f1556f39d7ee5986f9d4fd8a2573e75d929e82ac741fe0c833"


# The rule row.


def test_the_first_rule_row_holds_the_rule_and_the_latest_capture_set(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    older = bakeoff.write_capture()
    newer = bakeoff.write_capture((("Answer in words.", False, "usable"),))
    os.utime(bakeoff.captures() / f"capture-{older[:12]}.json", (1e9, 1e9))
    assert asyncio.run(write_rule(bakeoff.parts(), "", "")) == 0, bakeoff.said()
    [row] = bakeoff.steps()
    assert (row.stage, row.rule_hash, row.capture_set_hash) == (
        "rule",
        RULE_HASH,
        newer,
    )
    rule = read_bakeoff_rule(row.rule)
    assert not isinstance(rule, Failure)
    assert bakeoff_rule_hash(rule) == RULE_HASH
    assert row.capture_counts == {"capture_set_hash": newer, "requests": 1}
    assert (
        f"The rule row is in: {RULE_HASH}, on the capture set {newer}." in bakeoff.out()
    )


def test_a_rule_row_with_no_capture_set_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    assert asyncio.run(write_rule(bakeoff.parts(), "", "")) == 1
    assert "There is no capture set in" in bakeoff.said()
    assert bakeoff.steps() == []


def test_a_second_rule_row_names_the_one_it_replaces_and_why(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    assert asyncio.run(write_rule(bakeoff.parts(), "", "")) == 1
    assert "names the latest as the one it replaces" in bakeoff.said()
    assert asyncio.run(write_rule(bakeoff.parts(), RULE_HASH, "")) == 1
    assert len(bakeoff.steps()) == 1
    assert asyncio.run(write_rule(bakeoff.parts(), RULE_HASH, "A made-up reason.")) == 0
    [_, second] = bakeoff.steps()
    assert (second.replaces, second.why) == (RULE_HASH, "A made-up reason.")


def test_a_summary_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "captures"
    folder.mkdir()
    (folder / "capture-0123456789ab.json").write_text("[1, 2]")
    found = latest_capture(folder)
    assert isinstance(found, Failure)
    assert "is not a capture summary" in found.message
    (folder / "capture-0123456789ab.json").write_text("{not json")
    found = latest_capture(folder)
    assert isinstance(found, Failure)
    assert "could not be read" in found.message


# The choice and the table.


def measured(bakeoff: Bakeoff) -> None:
    code = asyncio.run(run(bakeoff.measure(), bakeoff.settings, "local", bakeoff.notes))
    assert code == 0, bakeoff.said()


def test_choose_waits_for_the_soak_and_writes_nothing(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    measured(bakeoff)
    assert asyncio.run(choose(bakeoff.parts())) == 1
    assert "Soak llama.cpp local first: step 5: llama.cpp local is next to soak." in (
        bakeoff.said()
    )
    assert [s.stage for s in bakeoff.steps()] == ["rule", "candidate"]


def test_with_no_candidate_left_none_is_chosen(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.footprint = Readings(40 * GB)
    measured(bakeoff)
    assert asyncio.run(choose(bakeoff.parts())) == 0, bakeoff.said()
    choice = bakeoff.steps()[-1]
    assert (choice.stage, choice.chosen, choice.server, choice.model) == (
        "choice",
        False,
        "",
        "",
    )
    assert choice.why == "step 2: no candidate is left"
    assert choice.capture_set_hash == bakeoff.steps()[0].capture_set_hash
    assert "Chosen: none, step 2: no candidate is left." in bakeoff.out()


def test_with_no_rule_row_choose_and_table_are_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    assert asyncio.run(choose(bakeoff.parts())) == 1
    assert asyncio.run(table(bakeoff.parts())) == 1
    assert bakeoff.said().count("There is no bake-off rule row.") == 2


def test_the_table_has_a_line_for_each_candidate_then_the_choice(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    measured(bakeoff)
    assert asyncio.run(table(bakeoff.parts())) == 0, bakeoff.said()
    [line, waiting] = bakeoff.out().splitlines()[-2:]
    assert line.startswith("llama.cpp local  llama.cpp b12826  Q4_K_M  hard: ")
    assert "broken_call_visible pass, prompt_over_window_caught pass" in line
    assert '"usable_replies":{"calls":2' in line
    assert line.endswith("  soak: none  in")
    assert waiting == "Not chosen yet: step 5: llama.cpp local is next to soak."


def test_a_candidate_that_is_out_is_listed_after_the_order_with_why(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    bakeoff.footprint = Readings(40 * GB)
    measured(bakeoff)
    assert asyncio.run(choose(bakeoff.parts())) == 0
    choice = bakeoff_choice(bakeoff.bench.rows())
    assert not isinstance(choice, Failure)
    lines = table_lines(bakeoff.steps(), choice)
    assert "step 2: its peak" in lines[0]
    assert lines[-1] == "Chosen: none, because step 2: no candidate is left."


# The capture set.


def counting_settings(tmp_path: Path, text: str = "") -> Path:
    path = tmp_path / "count.toml"
    path.write_text(text or only_local())
    return path


def chat_log(bakeoff: Bakeoff) -> None:
    kitchen = History("kitchen")
    answered_run(kitchen, "Kettle?", "run-1", MATCHED)
    answered_run(kitchen, "Milk?", "run-2", OTHER)
    bakeoff.log = asyncio.run(saved(kitchen))
    bakeoff.chat_ids = frozenset({MATCHED})


def captured(bakeoff: Bakeoff, tmp_path: Path, *count_with: Path) -> int:
    paths = list(count_with) or [counting_settings(tmp_path)]
    return asyncio.run(capture(bakeoff.parts(), bakeoff.settings, paths))


def test_the_capture_set_is_written_private_with_its_summary(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    assert captured(bakeoff, tmp_path) == 0, bakeoff.said()
    folder = bakeoff.captures()
    [lines] = folder.glob("capture-*.jsonl")
    [summary_file] = folder.glob("capture-*.json")
    text = lines.read_text()
    digest = set_hash(text)
    assert lines.name == f"capture-{digest[:12]}.jsonl"
    for path in (lines, summary_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    [request] = read_capture(text)
    part = request.request.messages[-1].parts[0]
    assert isinstance(part, TextPart) and part.text == "Kettle?"
    assert request.source == "chat"
    summary = json.loads(summary_file.read_text())
    assert summary == {
        "capture_set_hash": digest,
        "requests": 1,
        "chat": 1,
        "trials": 0,
        "runs_passed_over": 1,
        "held_out": 0,
        "repeats": 0,
        "over_ten_a_run": 0,
        "too_large": 0,
        "sizes": [1_000, 1_000, 1_000],
    }
    assert f'capture_set_hash: "{digest}"' in bakeoff.out()
    assert len(bakeoff.model.counted) == 1
    assert bakeoff.closed == 1
    assert bakeoff.log_closed == 1


def test_a_file_left_by_a_write_cut_short_is_made_private(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    assert captured(bakeoff, tmp_path) == 0, bakeoff.said()
    written = sorted(bakeoff.captures().iterdir())
    for path in written:
        path.unlink()
        left = path.with_name(path.name + ".new")
        left.write_text("Left over.")
        left.chmod(0o644)
    assert captured(bakeoff, tmp_path) == 0, bakeoff.said()
    assert sorted(bakeoff.captures().iterdir()) == written
    for path in written:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_request_too_large_for_the_rule_s_window_is_left_out(
    tmp_path: Path,
) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    # 49,537 and the 16,000 of the reply are one above 65,536.
    bakeoff.model.count = 49_537
    assert captured(bakeoff, tmp_path) == 0, bakeoff.said()
    [summary_file] = bakeoff.captures().glob("capture-*.json")
    summary = json.loads(summary_file.read_text())
    assert (summary["requests"], summary["too_large"]) == (0, 1)
    bakeoff.model.count = 49_536
    assert captured(bakeoff, tmp_path) == 0, bakeoff.said()
    newest = latest_capture(bakeoff.captures())
    assert not isinstance(newest, Failure)
    assert (newest["requests"], newest["too_large"]) == (1, 0)


def test_each_count_with_file_counts_each_request(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    first = counting_settings(tmp_path)
    second = tmp_path / "second.toml"
    second.write_text(only_local())
    assert captured(bakeoff, tmp_path, first, second) == 0, bakeoff.said()
    assert len(bakeoff.model.counted) == 2
    assert bakeoff.closed == 2


def two_local() -> str:
    second = (
        LOCAL.replace('name = "local"', 'name = "local-two"')
        .replace('model = "local"', 'model = "local-two"')
        .replace(":9931", ":9932")
    )
    text = only_local().replace('models = ["local"]', 'models = ["local", "local-two"]')
    at = text.index("[[routes]]")
    return text[:at] + second.lstrip() + "\n" + text[at:]


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (with_local(), "must hold exactly one model entry on this Mac"),
        (two_local(), "must hold exactly one model entry on this Mac"),
        (only_local().replace('count = "llama_cpp"', 'count = "vllm"'), "llama_cpp"),
    ],
)
def test_a_count_with_file_that_does_not_count_with_llama_cpp_is_refused(
    tmp_path: Path, text: str, said: str
) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    if 'count = "vllm"' in text:
        text = text.replace('server = "llama.cpp b12826"', 'server = "vllm-metal 0.1"')
    assert captured(bakeoff, tmp_path, counting_settings(tmp_path, text)) == 1
    assert said in bakeoff.said()
    assert not bakeoff.captures().exists()


def test_a_count_with_server_that_is_down_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    bakeoff.health.answer = DOWN
    assert captured(bakeoff, tmp_path) == 1
    assert "is not up: Refused." in bakeoff.said()
    assert bakeoff.closed == 1
    assert not bakeoff.captures().exists()


def test_no_count_with_file_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    assert asyncio.run(capture(bakeoff.parts(), bakeoff.settings, [])) == 1
    assert "Name at least one settings file to count with." in bakeoff.said()


def test_a_capture_folder_inside_a_checkout_is_refused(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    bakeoff.bench.folder = bakeoff.bench.root / "made-up-bench"
    assert captured(bakeoff, tmp_path) == 1
    assert "is inside a git checkout" in bakeoff.said()
    assert not bakeoff.captures().exists()
    assert bakeoff.model.counted == []


# The words of the command.


def parsed(*words: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bakeoff_parts(parser)
    return parser.parse_args(list(words))


def test_each_command_word_runs_its_command(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    chat_log(bakeoff)
    count = str(counting_settings(tmp_path))
    settings = str(bakeoff.settings)
    words = [
        ("capture", "--settings", settings, "--count-with", count),
        ("rule",),
        (
            "run",
            "--settings",
            settings,
            "--model",
            "local",
            "--notes",
            str(bakeoff.notes),
        ),
        ("table",),
    ]
    for each in words:
        code = asyncio.run(bakeoff_command(bakeoff.measure(), parsed(*each)))
        assert code == 0, (each, bakeoff.said())
    assert [s.stage for s in bakeoff.steps()] == ["rule", "candidate"]
    assert asyncio.run(bakeoff_command(bakeoff.measure(), parsed("choose"))) == 1
    soak = parsed("soak", "--settings", settings, "--model", "local")
    assert asyncio.run(bakeoff_command(bakeoff.measure(), soak)) == 0, bakeoff.said()
    assert asyncio.run(bakeoff_command(bakeoff.measure(), parsed("choose"))) == 0
    assert [s.stage for s in bakeoff.steps()][-2:] == ["soak", "choice"]


def test_a_rule_that_replaces_another_is_given_its_reason(tmp_path: Path) -> None:
    args = parsed("rule", "--replaces", "1" * 64, "--why", "A made-up reason.")
    assert (args.replaces, args.why) == ("1" * 64, "A made-up reason.")
    capture_args = parsed(
        "capture",
        "--settings",
        "a.toml",
        "--count-with",
        "b.toml",
        "--count-with",
        "c.toml",
    )
    assert capture_args.count_with == [Path("b.toml"), Path("c.toml")]


def test_the_table_reads_only_the_ledger(tmp_path: Path) -> None:
    bakeoff = Bakeoff(tmp_path)
    bakeoff.start()
    measured(bakeoff)
    before = len(bakeoff.out())
    assert asyncio.run(table(bakeoff.parts())) == 0
    lines = bakeoff.out()[before:]
    for path in bakeoff.captures().iterdir():
        path.unlink()
    assert asyncio.run(table(bakeoff.parts())) == 0
    assert bakeoff.out()[before + len(lines) :] == lines
