"""The rule that says whether a trial tested the agent, and the record's own form."""

import dataclasses
import json

import pytest

from lamplighter.bench import (
    Tokens,
    TrialEnds,
    TrialRecord,
    infra_reason,
    read_record,
    record_text,
)

WELL = TrialEnds("done", None, command_unavailable=False, swept=True, exported=True)
RECORD = TrialRecord(
    portable_config_id="p" * 64,
    config_id="c" * 64,
    code_revision="unknown",
    model="made-up-1",
    provider="made-up",
    session_id="bench-trial",
    run_id="run-1",
    run_reason="done",
    run_detail=None,
    gradable=True,
    infra_reason=None,
    left_out_of_bounds=None,
    steps=2,
    tool_calls=1,
    tokens=Tokens(10, 20, 30, 40),
    micro_dollars=1_234,
    ms=5_000,
    exported_events=12,
)


@pytest.mark.parametrize(
    ("reason", "detail"),
    [
        ("done", None),
        ("limit", "steps"),
        ("limit", "seconds"),
        ("limit", "tokens"),
        ("limit", "micro_dollars"),
        ("failed", "repeated_call"),
        ("failed", "refused"),
        ("failed", "empty_reply"),
        ("failed", "model_invalid_input"),
        ("failed", "model_too_large"),
    ],
)
def test_an_end_that_is_the_agents_doing_is_gradable(
    reason: str, detail: str | None
) -> None:
    assert (
        infra_reason(dataclasses.replace(WELL, run_reason=reason, run_detail=detail))
        is None
    )


@pytest.mark.parametrize(
    "kind",
    [
        "transient",
        "throttled",
        "timeout",
        "unavailable",
        "denied",
        "needs_user",
        "conflict",
        "cancelled",
        "fatal",
    ],
)
def test_a_model_outage_is_infra_by_its_kind(kind: str) -> None:
    ends = dataclasses.replace(WELL, run_reason="failed", run_detail=f"model_{kind}")
    assert infra_reason(ends) == f"model_{kind}"


@pytest.mark.parametrize(
    ("reason", "detail"),
    [
        (None, None),
        ("cancelled", None),
        ("failed", "config_changed"),
        ("failed", "model_made_up"),
        ("failed", None),
        ("made_up", None),
    ],
)
def test_any_other_end_is_a_harness_failure(
    reason: str | None, detail: str | None
) -> None:
    ends = dataclasses.replace(WELL, run_reason=reason, run_detail=detail)
    assert infra_reason(ends) == "harness_failure"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"command_unavailable": True}, "executor_unavailable"),
        ({"swept": False}, "sweep_failed"),
        ({"exported": False}, "export_failed"),
    ],
)
def test_a_lost_container_a_failed_sweep_and_a_failed_export_are_infra(
    change: dict[str, bool], reason: str
) -> None:
    assert infra_reason(dataclasses.replace(WELL, **change)) == reason


def test_when_several_parts_went_wrong_the_first_from_the_top_is_the_reason() -> None:
    every = TrialEnds("failed", "model_timeout", True, False, False)
    assert infra_reason(every) == "model_timeout"
    assert (
        infra_reason(dataclasses.replace(every, run_reason=None)) == "harness_failure"
    )
    later = dataclasses.replace(every, run_reason="done", run_detail=None)
    assert infra_reason(later) == "executor_unavailable"
    assert infra_reason(dataclasses.replace(later, command_unavailable=False)) == (
        "sweep_failed"
    )


def test_a_record_reads_back_as_itself() -> None:
    assert read_record(record_text(RECORD)) == RECORD
    infra = dataclasses.replace(RECORD, gradable=False, infra_reason="sweep_failed")
    assert read_record(record_text(infra)) == infra


def changed(**fields: object) -> str:
    plain = json.loads(record_text(RECORD))
    plain.update(fields)
    return json.dumps(plain)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        changed(gradable=False),
        changed(infra_reason="sweep_failed"),
        changed(infra_reason="made_up", gradable=False),
        changed(
            gradable=False, infra_reason="sweep_failed", left_out_of_bounds="too_deep"
        ),
        changed(left_out_of_bounds="made_up"),
        changed(steps=-1),
        changed(steps=True),
        changed(micro_dollars=-5),
        changed(tokens={"uncached": 1, "read": 1, "written": 1}),
        changed(v=2),
        changed(made_up=1),
    ],
)
def test_a_text_that_is_not_a_whole_record_is_none(text: str) -> None:
    assert read_record(text) is None


def test_a_record_without_a_field_is_none() -> None:
    plain = json.loads(record_text(RECORD))
    del plain["ms"]
    assert read_record(json.dumps(plain)) is None


def test_a_record_kept_before_the_models_were_named_reads_with_none_named() -> None:
    plain = json.loads(record_text(RECORD))
    del plain["models_called"]
    del plain["escalated"]
    assert read_record(json.dumps(plain)) == RECORD


def test_the_models_asked_and_an_escalation_read_back() -> None:
    asked = dataclasses.replace(
        RECORD, models_called=("local", "frontier"), escalated=True
    )
    assert read_record(record_text(asked)) == asked
    assert read_record(changed(models_called="local")) is None
    assert read_record(changed(escalated="yes")) is None


@pytest.mark.parametrize("fault", ["call_markup", "schema", "no_call"])
def test_a_reply_the_end_checks_refused_is_the_agents_fail(fault: str) -> None:
    ended = dataclasses.replace(
        WELL, run_reason="failed", run_detail=f"model_output:{fault}"
    )
    assert infra_reason(ended) is None
