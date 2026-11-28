import json

import pytest

from lamplighter.adapters.anthropic.errors import failure_for_response, may_retry
from lamplighter.ports import Failure, JsonValue


def error_body(
    error_type: str, details: JsonValue = None, message: str = "Made up."
) -> bytes:
    problem: dict[str, JsonValue] = {"type": error_type, "message": message}
    if details is not None:
        problem["details"] = details
    return json.dumps({"type": "error", "error": problem}).encode()


def of_kind(kind: str) -> Failure:
    status = {"transient": 500, "invalid_input": 400}[kind]
    return failure_for_response(status, {}, b"")


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, "invalid_input"),
        (401, "needs_user"),
        (402, "needs_user"),
        (403, "needs_user"),
        (404, "fatal"),
        (408, "transient"),
        (409, "transient"),
        (413, "too_large"),
        (418, "invalid_input"),
        (499, "invalid_input"),
        (429, "throttled"),
        (500, "transient"),
        (502, "transient"),
        (503, "transient"),
        (504, "timeout"),
        (529, "transient"),
        (599, "transient"),
        (204, "fatal"),
        (302, "fatal"),
        (399, "fatal"),
    ],
)
def test_each_status_maps_to_one_kind(status: int, kind: str) -> None:
    failure = failure_for_response(status, {}, error_body("made_up_error"))
    assert failure.kind == kind
    assert failure.effect == "none"
    assert failure.source == "ModelClient/anthropic"


@pytest.mark.parametrize("status", [500, 400, 529])
def test_a_timeout_error_is_a_timeout_whatever_the_status(status: int) -> None:
    failure = failure_for_response(status, {}, error_body("timeout_error"))
    assert failure.kind == "timeout"


def test_a_slow_down_carries_the_wait_the_server_asked_for() -> None:
    failure = failure_for_response(429, {"retry-after": "3"}, b"")
    assert (failure.kind, failure.retry_after_ms) == ("throttled", 3_000)


@pytest.mark.parametrize(
    ("header", "wait"),
    [("0", 0), ("1.5", 1_500), ("0.0004", 0), ("120", 120_000)],
)
def test_the_wait_is_read_as_seconds_and_kept_as_milliseconds(
    header: str, wait: int
) -> None:
    assert (
        failure_for_response(429, {"retry-after": header}, b"").retry_after_ms == wait
    )


@pytest.mark.parametrize(
    "header", ["soon", "-5", "", "1e3", "nan", "Thu, 26 Nov 2026 07:28:00 GMT", " 3"]
)
def test_a_wait_that_is_not_a_number_of_seconds_is_no_wait(header: str) -> None:
    failure = failure_for_response(429, {"retry-after": header}, b"")
    assert (failure.kind, failure.retry_after_ms) == ("throttled", None)


def test_a_slow_down_with_no_wait_is_still_a_slow_down() -> None:
    failure = failure_for_response(429, {}, error_body("rate_limit_error"))
    assert (failure.kind, failure.retry_after_ms) == ("throttled", None)


def test_only_a_slow_down_carries_a_wait() -> None:
    failure = failure_for_response(529, {"retry-after": "3"}, b"")
    assert (failure.kind, failure.retry_after_ms) == ("transient", None)


def test_a_spend_cap_needs_a_person_and_is_not_a_slow_down() -> None:
    details = {"error_code": "enforced_spend_limit_reached"}
    body = error_body("rate_limit_error", details)
    failure = failure_for_response(429, {}, body)
    assert failure.kind == "needs_user"
    assert failure.retry_after_ms is None
    assert failure.detail["error_code"] == "enforced_spend_limit_reached"


def test_the_spend_cap_code_only_counts_on_its_own_status() -> None:
    details = {"error_code": "enforced_spend_limit_reached"}
    failure = failure_for_response(400, {}, error_body("made_up_error", details))
    assert failure.kind == "invalid_input"


# Made up, in the words Anthropic's errors page gives for a reached limit.
SPEND_LIMIT = (
    "You have reached your specified API usage limits. You will regain access"
    " on 2026-12-01 at 00:00 UTC."
)


def test_a_reached_spend_limit_needs_a_person_and_is_not_a_wrong_request() -> None:
    body = error_body("invalid_request_error", message=SPEND_LIMIT)
    failure = failure_for_response(400, {}, body)
    assert failure.kind == "needs_user"
    assert not may_retry(failure, None)
    assert failure.detail["provider_message"] == SPEND_LIMIT
    assert failure.message == "Anthropic's API refused this account or key."


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "Made up."),
        (400, "The model said: You have reached your specified limit."),
        (400, "you have reached your specified API usage limits."),
        (422, SPEND_LIMIT),
    ],
)
def test_any_other_wrong_request_is_still_invalid_input(
    status: int, message: str
) -> None:
    body = error_body("invalid_request_error", message=message)
    assert failure_for_response(status, {}, body).kind == "invalid_input"


def test_the_detail_keeps_what_a_log_needs() -> None:
    headers = {"request-id": "req_made_up"}
    failure = failure_for_response(400, headers, error_body("invalid_request_error"))
    assert failure.detail == {
        "status": 400,
        "error_type": "invalid_request_error",
        "error_code": None,
        "request_id": "req_made_up",
        "provider_message": "Made up.",
    }


def test_the_message_is_the_adapters_own_and_never_the_servers() -> None:
    failure = failure_for_response(400, {}, error_body("invalid_request_error"))
    assert "Made up." not in failure.message
    assert failure.message


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>Bad gateway</html>",
        b"[1, 2]",
        b'{"error": "a plain word"}',
        b'{"error": {"type": "made_up_error", "details": "a plain word"}}',
        b"\xff\xfe not text",
    ],
)
def test_a_body_that_is_not_as_documented_still_gets_a_kind(body: bytes) -> None:
    failure = failure_for_response(502, {}, body)
    assert failure.kind == "transient"
    assert failure.detail["status"] == 502
    assert failure.detail["error_code"] is None


@pytest.mark.parametrize(
    ("kind", "retried"), [("transient", True), ("invalid_input", False)]
)
def test_the_kind_decides_a_retry_when_the_server_does_not_say(
    kind: str, retried: bool
) -> None:
    assert may_retry(of_kind(kind), None) is retried
    assert may_retry(of_kind(kind), "perhaps") is retried


@pytest.mark.parametrize("kind", ["transient", "invalid_input"])
def test_the_servers_word_on_a_retry_wins_over_the_kind(kind: str) -> None:
    assert may_retry(of_kind(kind), "true") is True
    assert may_retry(of_kind(kind), "false") is False
