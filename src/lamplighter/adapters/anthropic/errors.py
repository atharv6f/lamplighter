"""Maps what Anthropic's API reports as wrong to this repo's failure kinds."""

import re
from collections.abc import Mapping
from typing import Any

from lamplighter.adapters.anthropic.blocks import ADAPTER
from lamplighter.adapters.anthropic.wire import Unreadable, as_object, parse_object
from lamplighter.ports import Failure, FailureKind, JsonValue

_KIND_BY_ERROR_TYPE: dict[str, FailureKind] = {
    "invalid_request_error": "invalid_input",
    "authentication_error": "needs_user",
    "billing_error": "needs_user",
    "permission_error": "needs_user",
    "not_found_error": "fatal",
    "request_too_large": "too_large",
    "rate_limit_error": "throttled",
    "api_error": "transient",
    "timeout_error": "timeout",
    "overloaded_error": "transient",
}

_KIND_BY_STATUS: dict[int, FailureKind] = {
    400: "invalid_input",
    401: "needs_user",
    402: "needs_user",
    403: "needs_user",
    404: "fatal",
    408: "transient",
    409: "transient",
    413: "too_large",
    429: "throttled",
    504: "timeout",
}

# A failure of one of these kinds may clear by itself, so the call is tried again.
_RETRIED_KINDS: frozenset[FailureKind] = frozenset(
    {"transient", "throttled", "timeout", "unavailable"}
)

# The account has spent its monthly allowance. Waiting does not help: a person
# has to act. The status is the same 429 as a request to slow down.
_SPEND_CAP_CODE = "enforced_spend_limit_reached"
# A spend limit set in the Console has been reached. Anthropic's errors page
# says such requests "return HTTP 400 with error type invalid_request_error"
# and a message that starts with these words. Like the cap above, only a
# person can lift it, so the request itself is not what is wrong.
_SPEND_LIMIT_WORDS = "You have reached your specified"

_SECONDS = re.compile(r"\d+(\.\d+)?")

# The adapter writes these itself. A provider's own message may quote part of
# a request, so it goes into `detail` and never into `message`.
_MESSAGE_BY_KIND: dict[FailureKind, str] = {
    "transient": "Anthropic's API had a fault that may clear by itself.",
    "throttled": "Anthropic's API asked for a slower pace.",
    "timeout": "Anthropic's API did not answer in time.",
    "unavailable": "Anthropic's API could not be reached.",
    "invalid_input": "Anthropic's API rejected the request as wrong.",
    "too_large": "The request is larger than Anthropic's API accepts.",
    "needs_user": "Anthropic's API refused this account or key.",
    "fatal": "Anthropic's API answered in a way this adapter cannot use.",
}


def failure(
    kind: FailureKind, detail: dict[str, JsonValue], retry_after_ms: int | None = None
) -> Failure:
    """Build a Failure of this adapter. A model call never changes the world."""
    return Failure(
        kind=kind,
        effect="none",
        message=_MESSAGE_BY_KIND[kind],
        source=f"ModelClient/{ADAPTER}",
        retry_after_ms=retry_after_ms,
        detail=detail,
    )


def failure_in_stream(error: dict[str, Any]) -> Failure:
    """Give the Failure for an error object that arrived inside a stream.

    An error type this adapter has never seen is treated as a fault that may
    clear, because the API may add types and one new word must not stop the
    service.
    """
    error_type = error.get("type")
    kind = _KIND_BY_ERROR_TYPE.get(str(error_type), "transient")
    return failure(
        kind, {"error_type": error_type, "provider_message": error.get("message")}
    )


def failure_for_response(
    status: int, headers: Mapping[str, str], body: bytes
) -> Failure:
    """Give the Failure for a response whose status is not 200.

    The status decides the kind, so a body that is not the documented JSON
    still gets one. Header names must be in lower case.
    """
    error = _error_in(body)
    code = _object_or_empty(error.get("details")).get("error_code")
    kind = _kind_for(status, error.get("type"), code, error.get("message"))
    detail: dict[str, JsonValue] = {
        "status": status,
        "error_type": error.get("type"),
        "error_code": code,
        "request_id": headers.get("request-id"),
        "provider_message": error.get("message"),
    }
    wait = _milliseconds(headers.get("retry-after")) if kind == "throttled" else None
    return failure(kind, detail, wait)


def may_retry(failure: Failure, should_retry_header: str | None) -> bool:
    """Say whether a call that ended with this failure may be tried again.

    The server's `x-should-retry` header decides when the response has one.
    """
    if should_retry_header in ("true", "false"):
        return should_retry_header == "true"
    return failure.kind in _RETRIED_KINDS


def _error_in(body: bytes) -> dict[str, Any]:
    try:
        whole = parse_object(body.decode("utf-8", errors="replace"))
    except Unreadable:
        # A gateway in between may answer with a page that is not the API's JSON.
        return {}
    return _object_or_empty(whole.get("error"))


def _object_or_empty(value: object) -> dict[str, Any]:
    try:
        return as_object(value)
    except Unreadable:
        return {}


def _kind_for(
    status: int, error_type: object, code: object, message: object
) -> FailureKind:
    # The API also sends this error type with other statuses, such as 500.
    if error_type == "timeout_error":
        return "timeout"
    if status == 429 and code == _SPEND_CAP_CODE:
        return "needs_user"
    if (
        status == 400
        and isinstance(message, str)
        and message.startswith(_SPEND_LIMIT_WORDS)
    ):
        return "needs_user"
    if status in _KIND_BY_STATUS:
        return _KIND_BY_STATUS[status]
    if status >= 500:
        return "transient"
    # Any other refusal of the request itself. Sending it again changes nothing.
    return "invalid_input" if status >= 400 else "fatal"


def _milliseconds(seconds: str | None) -> int | None:
    # The header may also hold a date, which the API does not send. A value
    # that is not a plain number of seconds is treated as no value.
    if seconds is None or not _SECONDS.fullmatch(seconds):
        return None
    return round(float(seconds) * 1000)
