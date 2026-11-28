"""Maps what a chat completions server reports as wrong to this repo's failure kinds."""

from typing import Any, cast

from lamplighter.adapters.openai_chat.parts import ADAPTER
from lamplighter.adapters.wire_json import Unreadable, parse_object
from lamplighter.ports import Failure, FailureKind, JsonValue

_KIND_BY_STATUS: dict[int, FailureKind] = {
    400: "invalid_input",
    401: "needs_user",
    403: "needs_user",
    404: "fatal",
    408: "transient",
    413: "too_large",
    429: "throttled",
    503: "unavailable",
    504: "timeout",
}
# llama.cpp's error type for a prompt over the context window.
_TOO_LARGE_TYPE = "exceed_context_size_error"
# vLLM answers a prompt over its window with a 400 that names this field.
_TOO_LARGE_PARAM = "input_tokens"
_RETRIED: frozenset[FailureKind] = frozenset(
    {"transient", "throttled", "timeout", "unavailable"}
)
_MESSAGE_BY_KIND: dict[FailureKind, str] = {
    "transient": "The local model server had a fault that may clear by itself.",
    "throttled": "The local model server asked for a slower pace.",
    "timeout": "The local model server did not answer in time.",
    "unavailable": "The local model server could not be reached or is not ready.",
    "invalid_input": "The local model server rejected the request as wrong.",
    "too_large": "The request is larger than the local model's context window.",
    "needs_user": "The local model server refused the request's access.",
    "fatal": "The local model server answered in a way this adapter cannot use.",
}


def failure(kind: FailureKind, detail: dict[str, JsonValue]) -> Failure:
    """Build a Failure of this adapter. A model call never changes the world."""
    return Failure(
        kind=kind,
        effect="none",
        message=_MESSAGE_BY_KIND[kind],
        source=f"ModelClient/{ADAPTER}",
        detail=detail,
    )


def failure_for_error(status: int, error: object) -> Failure:
    """Give the Failure for the value under `error`, with the status it came with.

    The value is an object with a code, a message, a type and a field, as
    llama.cpp and vLLM send it, or one text, as MLX sends it. Text maps by
    the status alone.
    """
    if not isinstance(error, dict):
        return _by_status(status, {"provider_message": str(error)})
    fields = cast(dict[str, Any], error)
    code = fields.get("code")
    if type(code) is int:
        status = code
    detail: dict[str, JsonValue] = {
        "error_type": str(fields.get("type")),
        "provider_message": str(fields.get("message")),
    }
    if fields.get("type") == _TOO_LARGE_TYPE:
        return failure("too_large", {"status": status, **detail})
    if status == 400 and fields.get("param") == _TOO_LARGE_PARAM:
        return failure("too_large", {"status": status, **detail})
    return _by_status(status, detail)


def failure_for_response(status: int, body: bytes) -> Failure:
    """Give the Failure for a response whose status is not 200."""
    try:
        error: object = parse_object(body.decode("utf-8", "replace")).get("error")
    except Unreadable:
        error = None
    if error is None:
        return _by_status(status, {})
    return failure_for_error(status, error)


def may_retry(why: Failure) -> bool:
    """Say whether a call that ended with this failure may be tried again.

    A reply the model itself got wrong is not tried again here, even when it
    gave nothing before the fault: the same request would most likely give
    the same reply. The loop's own one retry still sees it.
    """
    return why.kind in _RETRIED and "model_output" not in why.detail


def _by_status(status: int, detail: dict[str, JsonValue]) -> Failure:
    if status in _KIND_BY_STATUS:
        kind = _KIND_BY_STATUS[status]
    else:
        kind = "transient" if status >= 500 else "invalid_input"
    return failure(kind, {"status": status, **detail})
