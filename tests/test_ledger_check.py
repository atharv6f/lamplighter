"""The book's rules are tried in order, and the count of held-out uses is its own."""

import dataclasses

from lamplighter.ports import Failure, LedgerRow, held_out_uses, refused_row
from tests.ledger_kit import decision, loose, plan, start, whole_experiment


def _answer(rows: list[LedgerRow], row: LedgerRow) -> tuple[str, str] | None:
    found = refused_row(rows, row)
    return None if found is None else (found.kind, found.message)


def test_the_shape_is_tried_before_the_row_id() -> None:
    first = plan()
    again = dataclasses.replace(plan("Bad-Name"), row_id=first.row_id)
    answer = _answer([first], again)
    assert answer is not None
    assert answer[0] == "invalid_input"
    assert "not one of the book's" in answer[1]


def test_the_row_id_is_tried_before_a_second_plan() -> None:
    first = plan()
    answer = _answer([first], dataclasses.replace(plan(), row_id=first.row_id))
    assert answer is not None
    assert "row id" in answer[1]


def test_a_second_start_is_a_conflict_before_it_disagrees_with_its_plan() -> None:
    rows: list[LedgerRow] = [plan(), start()]
    answer = _answer(rows, start(split="held_out"))
    assert answer is not None
    assert answer[0] == "conflict"


def test_a_decision_without_its_run_sets_is_refused_before_its_count() -> None:
    rows = whole_experiment()[:3]
    answer = _answer(rows, decision(uses=5))
    assert answer is not None
    assert "lacks a run set" in answer[1]


def test_the_count_is_tried_before_a_correction_rule_could_apply() -> None:
    rows = whole_experiment()
    answer = _answer(rows, decision(uses=1))
    assert answer == ("invalid_input", "The held-out uses are 0.")


def test_two_held_out_experiments_and_one_on_the_dev_set() -> None:
    rows: list[LedgerRow] = [
        *whole_experiment("held-one", "held_out"),
        # Started and cut off before any run set: still a use.
        plan("held-two", "held_out"),
        start("held-two", "baseline", "held_out"),
        *whole_experiment("on-dev", "dev"),
        loose(),
    ]
    assert held_out_uses(rows) == 2
    refused = refused_row(rows, decision("on-dev", uses=0))
    assert isinstance(refused, Failure)
    assert refused_row(rows, decision("on-dev", uses=2)) is None


def test_a_plan_on_the_held_out_set_is_no_use_until_a_side_starts() -> None:
    rows: list[LedgerRow] = [plan("planned", "held_out")]
    assert held_out_uses(rows) == 0
    assert held_out_uses([*rows, start("planned", "baseline", "held_out")]) == 1


def test_an_experiment_with_both_sides_started_is_one_use() -> None:
    assert held_out_uses(whole_experiment("same", "held_out")) == 1
