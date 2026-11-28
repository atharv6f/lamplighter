"""The rule from a trial's record and how its check ended to pass, fail or infra."""

import dataclasses

import pytest

from lamplighter.bench import (
    BoundWord,
    InfraReason,
    TrialOutcome,
    check_outcome,
    harness_outcome,
)
from lamplighter.ports import CheckEnd
from tests.bench.test_trial_record import RECORD

CHECK_ENDS: list[tuple[CheckEnd, TrialOutcome]] = [
    ("passed", TrialOutcome("pass", None)),
    ("failed", TrialOutcome("fail", None)),
    ("timed_out", TrialOutcome("fail", "check_timed_out")),
    ("unreadable_reward", TrialOutcome("fail", "check_unreadable_reward")),
    ("none", TrialOutcome("infra", "no_check_result")),
]
ALL_CHECKS: list[CheckEnd] = [check for check, _ in CHECK_ENDS]


@pytest.mark.parametrize("check", ALL_CHECKS)
def test_no_record_is_infra_whatever_the_check_says(check: CheckEnd) -> None:
    assert harness_outcome(None, check) == TrialOutcome("infra", "no_record")


@pytest.mark.parametrize("check", ALL_CHECKS)
@pytest.mark.parametrize(
    "reason", ["budget_reached", "harness_failure", "model_unavailable", "sweep_failed"]
)
def test_a_record_that_is_not_gradable_is_infra_with_its_reason(
    reason: InfraReason, check: CheckEnd
) -> None:
    record = dataclasses.replace(RECORD, gradable=False, infra_reason=reason)
    assert harness_outcome(record, check) == TrialOutcome("infra", reason)


@pytest.mark.parametrize("check", ALL_CHECKS)
@pytest.mark.parametrize(
    "word", ["too_large", "too_many_entries", "too_deep", "unreadable"]
)
def test_what_the_agent_left_out_of_bounds_is_a_fail_even_on_a_pass(
    word: BoundWord, check: CheckEnd
) -> None:
    record = dataclasses.replace(RECORD, left_out_of_bounds=word)
    assert harness_outcome(record, check) == TrialOutcome("fail", word)


@pytest.mark.parametrize(("check", "outcome"), CHECK_ENDS)
def test_a_gradable_trial_in_bounds_is_judged_by_its_check(
    check: CheckEnd, outcome: TrialOutcome
) -> None:
    assert harness_outcome(RECORD, check) == outcome


@pytest.mark.parametrize(("check", "outcome"), CHECK_ENDS)
def test_a_baseline_is_judged_by_its_check_alone(
    check: CheckEnd, outcome: TrialOutcome
) -> None:
    assert check_outcome(check) == outcome


def test_the_first_row_that_holds_decides() -> None:
    # A record read from its file is never both, so the record is built here.
    both = dataclasses.replace(
        RECORD,
        gradable=False,
        infra_reason="sweep_failed",
        left_out_of_bounds="too_deep",
    )
    assert harness_outcome(both, "passed") == TrialOutcome("infra", "sweep_failed")
