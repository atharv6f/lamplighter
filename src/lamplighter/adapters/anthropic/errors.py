"""Maps what Anthropic's API reports as wrong to this repo's failure kinds."""

from typing import Any

from lamplighter.adapters.anthropic.blocks import ADAPTER
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
