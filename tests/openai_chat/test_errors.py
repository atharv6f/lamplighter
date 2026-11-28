"""How the local adapter maps what a server reports as wrong to failure kinds."""

import dataclasses
import json

import httpx
import pytest

from lamplighter.adapters.openai_chat.errors import failure_for_response, may_retry
from lamplighter.ports import ErrorEvent, Message, ModelRequest, TextPart
from tests.openai_chat.fake_server import answering, client_for
from tests.openai_chat.wire_text import chunk, data_line
from tests.streams import collect

ROWS = [
    (400, "invalid_input"),
    (401, "needs_user"),
    (403, "needs_user"),
    (404, "fatal"),
    (408, "transient"),
    (413, "too_large"),
    (418, "invalid_input"),
    (429, "throttled"),
    (500, "transient"),
    (502, "transient"),
    (503, "unavailable"),
    (504, "timeout"),
]
REQUEST = ModelRequest(
    system=(),
    messages=(Message(role="user", parts=(TextPart(text="Hello."),)),),
    tools=(),
    max_output=64,
    cache_hints=(),
    deadline_ms=5_000,
    retries=0,
)


def object_shape(
    status: int, error_type: str = "made_up", param: object = None
) -> bytes:
    error = {"code": status, "message": "Made up.", "type": error_type, "param": param}
    return json.dumps({"error": error}).encode()


def text_shape() -> bytes:
    return json.dumps({"error": "Made up."}).encode()


@pytest.mark.parametrize(("status", "kind"), ROWS)
def test_each_status_maps_to_one_kind_in_the_object_shape(
    status: int, kind: str
) -> None:
    failure = failure_for_response(status, object_shape(status))
    assert failure.kind == kind
    assert failure.effect == "none"
    assert failure.source == "ModelClient/openai_chat"


@pytest.mark.parametrize(("status", "kind"), ROWS)
def test_each_status_maps_to_one_kind_in_the_text_shape(status: int, kind: str) -> None:
    assert failure_for_response(status, text_shape()).kind == kind


@pytest.mark.parametrize(("status", "kind"), ROWS)
def test_each_status_maps_with_no_body_at_all(status: int, kind: str) -> None:
    assert failure_for_response(status, b"").kind == kind


def test_a_code_in_the_body_stands_in_for_the_status() -> None:
    assert failure_for_response(500, object_shape(429)).kind == "throttled"


def test_a_prompt_over_the_window_of_llama_cpp_is_too_large() -> None:
    body = object_shape(400, "exceed_context_size_error")
    assert failure_for_response(400, body).kind == "too_large"


def test_a_prompt_over_the_window_of_vllm_is_too_large() -> None:
    body = object_shape(400, "BadRequestError", "input_tokens")
    assert failure_for_response(400, body).kind == "too_large"
    other = object_shape(400, "BadRequestError", "messages")
    assert failure_for_response(400, other).kind == "invalid_input"


def test_the_servers_words_go_into_the_detail_and_never_the_message() -> None:
    failure = failure_for_response(400, object_shape(400))
    assert "Made up." not in failure.message
    assert failure.detail["provider_message"] == "Made up."
    assert failure.detail["status"] == 400


@pytest.mark.parametrize(("status", "kind"), ROWS)
def test_an_error_inside_a_stream_maps_by_its_code(status: int, kind: str) -> None:
    error = {"code": status, "message": "Made up.", "type": "made_up"}
    body = chunk({"content": "Tea"}) + data_line({"error": error})
    serve, _ = answering("vllm", httpx.Response(200, content=body))
    end = collect(client_for(serve, "vllm"), REQUEST)[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == kind


def test_an_error_text_inside_a_stream_is_transient() -> None:
    body = chunk({"content": "Tea"}) + data_line({"error": "Made up."})
    serve, _ = answering("mlx_lm", httpx.Response(200, content=body))
    end = collect(client_for(serve, "mlx_lm"), REQUEST)[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"


@pytest.mark.parametrize(
    ("kind", "retried"),
    [
        ("transient", True),
        ("throttled", True),
        ("timeout", True),
        ("unavailable", True),
        ("invalid_input", False),
        ("too_large", False),
        ("needs_user", False),
        ("fatal", False),
    ],
)
def test_only_four_kinds_are_tried_again(kind: str, retried: bool) -> None:
    status = {
        "transient": 500,
        "throttled": 429,
        "timeout": 504,
        "unavailable": 503,
        "invalid_input": 400,
        "too_large": 413,
        "needs_user": 401,
        "fatal": 404,
    }[kind]
    assert may_retry(failure_for_response(status, b"")) is retried


def test_a_refused_connection_is_unavailable_and_tried_again() -> None:
    refused = httpx.ConnectError("made-up refusal")
    serve, server = answering("llama_cpp", refused, refused)
    retrying = dataclasses.replace(REQUEST, retries=1)
    end = collect(client_for(serve, "llama_cpp"), retrying)[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "unavailable"
    assert len(server.requests) == 2


def test_a_connection_dropped_after_it_was_made_is_transient() -> None:
    serve, _ = answering("vllm", httpx.ReadError("made-up drop"))
    end = collect(client_for(serve, "vllm"), REQUEST)[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
