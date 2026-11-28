"""The promises of the book of experiments, checked on every adapter of it."""

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FileLedger
from lamplighter.adapters.in_memory import InMemoryLedger
from lamplighter.ports import ExperimentLedger, Failure, LedgerRow
from tests.ledger_kit import (
    BAKEOFF_RULE,
    CANDIDATE_ID,
    bakeoff_candidate,
    bakeoff_choice,
    bakeoff_rule,
    bakeoff_soak,
    correction,
    decision,
    loose,
    plan,
    run_set,
    start,
    whole_experiment,
)

BOOKS: dict[str, Callable[[Path], ExperimentLedger]] = {
    "in_memory": lambda _: InMemoryLedger(),
    "file": lambda folder: FileLedger(folder / "ledger.jsonl"),
}


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


@pytest.fixture(params=list(BOOKS.values()), ids=list(BOOKS))
def book(request: pytest.FixtureRequest, tmp_path: Path) -> ExperimentLedger:
    return request.param(tmp_path)


def rows(book: ExperimentLedger) -> Sequence[LedgerRow]:
    found = run(book.rows())
    assert not isinstance(found, Failure), found
    return found


def added(book: ExperimentLedger, *new: LedgerRow) -> None:
    for row in new:
        answer = run(book.append(row))
        assert answer is None, answer


def refused(book: ExperimentLedger, row: LedgerRow, kind: str) -> None:
    before = rows(book)
    answer = run(book.append(row))
    assert isinstance(answer, Failure), answer
    assert (answer.kind, answer.effect) == (kind, "none")
    assert rows(book) == before


def test_a_new_book_has_no_rows(book: ExperimentLedger) -> None:
    assert list(rows(book)) == []


def test_rows_come_back_in_the_order_they_were_added(book: ExperimentLedger) -> None:
    every = [*whole_experiment(), decision()]
    added(book, *every)
    fix = correction(every[2].row_id)
    added(book, fix)
    assert list(rows(book)) == [*every, fix]


@pytest.mark.parametrize(
    "broken",
    [
        lambda: dataclasses.replace(plan(), v=2),
        lambda: plan("Made-Up"),
        lambda: plan("a" * 41),
        lambda: plan("made--up"),
        lambda: plan(""),
        lambda: dataclasses.replace(plan(), config_ids=("one",)),
        lambda: dataclasses.replace(start(), config_ids=()),
        lambda: dataclasses.replace(correction("x"), config_ids=("one",)),
        lambda: dataclasses.replace(
            start(), body=dataclasses.replace(start().body, run_set="other--baseline")
        ),
        lambda: dataclasses.replace(
            run_set(), body=dataclasses.replace(run_set().body, side="")
        ),
        lambda: loose("Not-A-Name"),
        lambda: loose("made-up--baseline"),
        lambda: dataclasses.replace(
            run_set(),
            body=dataclasses.replace(run_set().body, run_set={"run_set": "x"}),
        ),
        lambda: dataclasses.replace(
            decision(), body=dataclasses.replace(decision().body, decision={})
        ),
        lambda: dataclasses.replace(
            plan(), body=dataclasses.replace(plan().body, rule={"alpha": 0.05})
        ),
        lambda: dataclasses.replace(bakeoff_rule(), experiment="made-up"),
        lambda: dataclasses.replace(bakeoff_rule(), config_ids=("one",)),
        lambda: bakeoff_rule(rule_hash="not-a-hash"),
    ],
)
def test_a_row_not_of_the_books_shape_is_refused(
    book: ExperimentLedger, broken: Callable[[], LedgerRow]
) -> None:
    refused(book, broken(), "invalid_input")


def test_a_row_id_already_used_is_a_conflict(book: ExperimentLedger) -> None:
    first = plan("one")
    added(book, first)
    refused(book, dataclasses.replace(plan("two"), row_id=first.row_id), "conflict")


def test_a_second_plan_start_run_set_or_decision_is_a_conflict(
    book: ExperimentLedger,
) -> None:
    added(book, *whole_experiment(), decision())
    for again in (
        plan(),
        start(side="baseline"),
        start(side="candidate"),
        run_set(side="baseline"),
        run_set(side="candidate"),
        decision(),
    ):
        refused(book, again, "conflict")


def test_a_corrected_run_set_still_blocks_a_second_one(book: ExperimentLedger) -> None:
    every = whole_experiment()
    added(book, *every, correction(every[2].row_id))
    refused(book, run_set(side="baseline"), "conflict")
    added(book, decision())


def test_a_start_needs_its_plan_and_must_agree_with_it(book: ExperimentLedger) -> None:
    refused(book, start(), "invalid_input")
    added(book, plan())
    refused(book, start(split="held_out"), "invalid_input")
    refused(book, start(side="baseline", config_id=CANDIDATE_ID), "invalid_input")
    added(book, start())


def test_a_run_set_of_an_experiment_needs_its_sides_start(
    book: ExperimentLedger,
) -> None:
    added(book, plan(), start(side="baseline"))
    refused(book, run_set(side="candidate"), "invalid_input")
    refused(book, run_set(task_set_hash="u" * 64), "invalid_input")
    refused(book, run_set(split="held_out"), "invalid_input")
    refused(book, run_set(config_id=CANDIDATE_ID), "invalid_input")
    added(book, run_set())


def test_a_loose_run_set_may_not_use_the_held_out_set(book: ExperimentLedger) -> None:
    refused(book, loose(split="held_out"), "invalid_input")
    added(book, loose(), loose("another"))


def test_a_decision_needs_its_plan_with_the_rule_and_both_run_sets(
    book: ExperimentLedger,
) -> None:
    refused(book, decision(), "invalid_input")
    every = whole_experiment()
    added(book, *every[:4])
    refused(book, decision(), "invalid_input")
    added(book, every[4])
    refused(book, decision(rule_hash="q" * 64), "invalid_input")
    added(book, decision())


def test_a_decision_carries_the_books_count_of_held_out_uses(
    book: ExperimentLedger,
) -> None:
    added(book, *whole_experiment("held", "held_out"))
    refused(book, decision("held", uses=0), "invalid_input")
    refused(book, decision("held", uses=2), "invalid_input")
    added(book, decision("held", uses=1))


def test_a_correction_needs_the_row_it_corrects(book: ExperimentLedger) -> None:
    refused(book, correction("row-that-is-not-there"), "invalid_input")
    first = plan()
    added(book, first)
    refused(book, correction(first.row_id, experiment="other"), "invalid_input")
    added(book, correction(first.row_id))


def test_a_correction_of_a_loose_run_set_names_no_experiment(
    book: ExperimentLedger,
) -> None:
    lone = loose()
    added(book, lone, correction(lone.row_id, experiment=""))


def test_a_start_named_for_another_run_set_is_refused(book: ExperimentLedger) -> None:
    added(book, plan())
    wrong = start()
    body = dataclasses.replace(wrong.body, run_set="made-up--candidate")
    refused(book, dataclasses.replace(wrong, body=body), "invalid_input")


def test_a_loose_run_set_with_a_side_is_refused(book: ExperimentLedger) -> None:
    lone = loose()
    body = dataclasses.replace(lone.body, side="baseline")
    refused(book, dataclasses.replace(lone, body=body), "invalid_input")


def test_a_decision_with_no_rule_hash_is_refused(book: ExperimentLedger) -> None:
    added(book, *whole_experiment())
    bare = decision()
    body = dataclasses.replace(bare.body, decision={"outcome": "keep"})
    refused(book, dataclasses.replace(bare, body=body), "invalid_input")


NEW_RULE = "2" * 64


def test_a_whole_bake_off_comes_back_as_it_was_added(book: ExperimentLedger) -> None:
    every = [
        bakeoff_rule(),
        bakeoff_candidate("llama.cpp"),
        bakeoff_candidate("mlx-lm"),
        bakeoff_soak(False, "llama.cpp"),
        bakeoff_soak(True, "mlx-lm"),
        bakeoff_choice("mlx-lm"),
    ]
    added(book, *every)
    assert list(rows(book)) == every


def test_a_first_rule_row_replaces_none(book: ExperimentLedger) -> None:
    refused(book, bakeoff_rule(replaces=NEW_RULE, why="Made up."), "invalid_input")
    added(book, bakeoff_rule())


def test_a_second_rule_row_names_the_latest_and_says_why(
    book: ExperimentLedger,
) -> None:
    added(book, bakeoff_rule())
    refused(book, bakeoff_rule(NEW_RULE), "invalid_input")
    refused(book, bakeoff_rule(NEW_RULE, replaces=BAKEOFF_RULE), "invalid_input")
    refused(book, bakeoff_rule(NEW_RULE, NEW_RULE, "Made up."), "invalid_input")
    added(book, bakeoff_rule(NEW_RULE, BAKEOFF_RULE, "The limit was lower."))
    # The first is no longer the latest.
    third = "3" * 64
    refused(book, bakeoff_rule(third, BAKEOFF_RULE, "Made up."), "invalid_input")
    added(book, bakeoff_rule(third, NEW_RULE, "Made up."))


@pytest.mark.parametrize("measured", [bakeoff_candidate, bakeoff_soak])
def test_a_measured_row_is_under_the_latest_rule_and_its_capture_set(
    book: ExperimentLedger, measured: Callable[..., LedgerRow]
) -> None:
    refused(book, measured(), "invalid_input")
    added(book, bakeoff_rule())
    refused(book, measured(capture="f" * 64), "invalid_input")
    refused(book, measured(rule_hash=NEW_RULE), "invalid_input")
    added(book, measured())
    added(book, bakeoff_rule(NEW_RULE, BAKEOFF_RULE, "Made up."))
    refused(book, measured(), "invalid_input")
    added(book, measured(rule_hash=NEW_RULE))


def test_a_chosen_winner_has_a_passing_soak_under_the_same_rule(
    book: ExperimentLedger,
) -> None:
    added(book, bakeoff_rule(), bakeoff_candidate(), bakeoff_candidate("mlx-lm"))
    refused(book, bakeoff_choice(), "invalid_input")
    added(book, bakeoff_soak(False))
    refused(book, bakeoff_choice(), "invalid_input")
    added(book, bakeoff_soak(True, "mlx-lm"))
    refused(book, bakeoff_choice(), "invalid_input")
    refused(book, bakeoff_choice("mlx-lm", "made-up-35b"), "invalid_input")
    other = bakeoff_choice("mlx-lm")
    weights = dataclasses.replace(other.body, weights_sha256="9" * 64)
    refused(book, dataclasses.replace(other, body=weights), "invalid_input")
    refused(book, bakeoff_choice("mlx-lm", rule_hash=NEW_RULE), "invalid_input")
    added(book, bakeoff_choice("mlx-lm"))


def test_a_choice_of_none_needs_no_soak(book: ExperimentLedger) -> None:
    added(book, bakeoff_rule(), bakeoff_choice(""))


def test_a_soak_under_an_earlier_rule_does_not_choose_under_a_new_one(
    book: ExperimentLedger,
) -> None:
    added(book, bakeoff_rule(), bakeoff_soak())
    added(book, bakeoff_rule(NEW_RULE, BAKEOFF_RULE, "Made up."))
    refused(book, bakeoff_choice(rule_hash=NEW_RULE), "invalid_input")
    added(book, bakeoff_choice())
