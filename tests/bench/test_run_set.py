"""A run set is built from what the job runner recorded, and only when it is whole."""

import dataclasses

import pytest

from lamplighter.bench import (
    HARNESS,
    NotWhole,
    RunSet,
    RunSetPlan,
    TaskCount,
    Tokens,
    record_text,
    run_set,
)
from lamplighter.ports import RecordedTokens, TrialFacts
from tests.bench.test_trial_record import RECORD

PLAN = RunSetPlan(
    run_set="made-up-run",
    harness=HARNESS,
    portable_config_id="p" * 64,
    code_revision="unknown",
    task_set_hash="t" * 64,
    split="dev",
    trials_per_task=3,
    tasks=("beta", "alpha"),
    model="made-up-1",
    provider="made-up",
)
BASELINE = dataclasses.replace(PLAN, harness="made-up-baseline", portable_config_id="")
FACTS = TrialFacts(
    task="alpha",
    runner_name="alpha__aaa",
    started_at="2026-11-27T10:00:00+00:00",
    ms=60_000,
    check="passed",
    stopped_by="",
    task_folder_hash="h" * 64,
    harness_version="1.0",
    tokens=RecordedTokens(input=100, cache_read=60, output=7),
    micro_dollars=999,
    record=record_text(RECORD),
    folder="/made/up/alpha__aaa",
)


def _facts(task: str, name: str, started_at: str) -> TrialFacts:
    return dataclasses.replace(
        FACTS, task=task, runner_name=name, started_at=started_at
    )


def _whole() -> list[TrialFacts]:
    return [
        _facts(task, f"{task}__{n}", f"2026-11-27T10:00:0{n}+00:00")
        for task in ("alpha", "beta")
        for n in range(3)
    ]


def _built(plan: RunSetPlan, found: list[TrialFacts]) -> RunSet:
    built = run_set(plan, found)
    assert isinstance(built, RunSet)
    return built


def test_a_whole_run_set_has_the_plans_values_and_one_entry_per_trial() -> None:
    built = _built(PLAN, _whole())
    assert (built.run_set, built.harness, built.harness_version) == (
        "made-up-run",
        HARNESS,
        "1.0",
    )
    assert (built.portable_config_id, built.code_revision) == ("p" * 64, "unknown")
    assert (built.task_set_hash, built.split, built.trials_per_task) == (
        "t" * 64,
        "dev",
        3,
    )
    assert (built.model, built.provider) == ("made-up-1", "made-up")
    assert [(t.task, t.attempt) for t in built.trials] == [
        ("alpha", 1),
        ("alpha", 2),
        ("alpha", 3),
        ("beta", 1),
        ("beta", 2),
        ("beta", 3),
    ]


def test_attempts_follow_the_start_as_a_point_in_time_and_not_as_text() -> None:
    # As text, the first sorts last: "12:..." is after "10:...". As points in
    # time it is half an hour before the second, and the third is written
    # with a fraction of a second and in another form of the zone.
    found = [
        _facts("alpha", "alpha__a", "2026-11-27T12:00:00+02:00"),
        _facts("alpha", "alpha__b", "2026-11-27T10:30:00Z"),
        _facts("alpha", "alpha__c", "2026-11-27T10:15:00.500000+00:00"),
    ]
    plan = dataclasses.replace(PLAN, tasks=("alpha",))
    built = _built(plan, found)
    assert [(t.runner_name, t.attempt) for t in built.trials] == [
        ("alpha__a", 1),
        ("alpha__c", 2),
        ("alpha__b", 3),
    ]


def test_a_tie_in_start_is_broken_by_the_runners_name() -> None:
    at = "2026-11-27T10:00:00+00:00"
    found = [
        _facts("alpha", "alpha__zz", at),
        _facts("alpha", "alpha__xx", at),
        _facts("alpha", "alpha__yy", "2026-11-27T09:00:00+00:00"),
    ]
    plan = dataclasses.replace(PLAN, tasks=("alpha",))
    built = _built(plan, found)
    assert [t.runner_name for t in built.trials] == [
        "alpha__yy",
        "alpha__xx",
        "alpha__zz",
    ]


def test_a_task_with_a_trial_missing_is_not_whole() -> None:
    found = _whole()[:-1]
    assert run_set(PLAN, found) == NotWhole((TaskCount("beta", 2),), ())


def test_a_task_with_no_trial_is_not_whole() -> None:
    found = [f for f in _whole() if f.task == "alpha"]
    assert run_set(PLAN, found) == NotWhole((TaskCount("beta", 0),), ())


def test_a_task_with_a_trial_too_many_is_not_whole() -> None:
    found = [*_whole(), _facts("alpha", "alpha__9", "2026-11-27T11:00:00+00:00")]
    assert run_set(PLAN, found) == NotWhole((TaskCount("alpha", 4),), ())


def test_a_trial_of_a_task_not_planned_is_not_whole() -> None:
    extra = [
        _facts("gamma", f"gamma__{n}", f"2026-11-27T11:00:0{n}+00:00") for n in range(3)
    ]
    assert run_set(PLAN, [*_whole(), *extra]) == NotWhole((TaskCount("gamma", 3),), ())


def test_trials_of_two_harness_versions_are_not_one_run_set() -> None:
    found = _whole()
    found[0] = dataclasses.replace(found[0], harness_version="2.0")
    assert run_set(PLAN, found) == NotWhole((), ("1.0", "2.0"))


def test_no_trials_at_all_are_not_whole() -> None:
    assert run_set(PLAN, []) == NotWhole(
        (TaskCount("alpha", 0), TaskCount("beta", 0)), ()
    )


def test_a_harness_trials_numbers_are_its_records() -> None:
    built = _built(PLAN, _whole())
    first = built.trials[0]
    assert (first.outcome, first.reason, first.run_reason) == ("pass", None, "done")
    assert (first.steps, first.tool_calls) == (2, 1)
    assert (first.tokens, first.micro_dollars) == (Tokens(10, 20, 30, 40), 1_234)
    # The time is the job runner's, which the record's is not.
    assert (first.ms, first.task_folder_hash) == (60_000, "h" * 64)


@pytest.mark.parametrize(
    "record",
    [
        None,
        "not json",
        record_text(dataclasses.replace(RECORD, gradable=False, infra_reason=None)),
    ],
)
def test_a_harness_trial_with_no_record_that_can_be_read_is_no_record(
    record: str | None,
) -> None:
    found = _whole()
    found[0] = dataclasses.replace(found[0], record=record)
    first = _built(PLAN, found).trials[0]
    assert (first.outcome, first.reason, first.run_reason) == (
        "infra",
        "no_record",
        None,
    )
    assert (first.steps, first.tool_calls) == (0, 0)
    assert (first.tokens, first.micro_dollars) == (Tokens(40, 60, 0, 7), 999)


def test_a_baseline_is_judged_by_its_check_and_counted_by_the_job_runner() -> None:
    found = [dataclasses.replace(f, record=None, check="failed") for f in _whole()]
    found[0] = dataclasses.replace(found[0], check="passed", tokens=None)
    built = _built(BASELINE, found)
    assert built.portable_config_id == ""
    first, second = built.trials[:2]
    assert (first.outcome, first.reason, first.run_reason) == ("pass", None, None)
    assert (first.tokens, first.micro_dollars) == (Tokens(0, 0, 0, 0), 999)
    assert (second.outcome, second.tokens) == ("fail", Tokens(40, 60, 0, 7))


def test_a_baseline_never_reads_a_record() -> None:
    # A record that says not gradable would make a harness trial infra.
    stopped = dataclasses.replace(
        RECORD, gradable=False, infra_reason="harness_failure"
    )
    found = [dataclasses.replace(f, record=record_text(stopped)) for f in _whole()]
    built = _built(BASELINE, found)
    assert {t.outcome for t in built.trials} == {"pass"}
    assert {t.steps for t in built.trials} == {0}
