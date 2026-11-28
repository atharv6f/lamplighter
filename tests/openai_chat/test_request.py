"""How the local adapter writes a request in the chat completions format."""

import asyncio
import dataclasses
import json
from typing import Any

import httpx

from lamplighter.adapters.openai_chat.parts import render_request
from lamplighter.core import fold_reply
from lamplighter.ports import (
    CacheHint,
    Message,
    ModelRequest,
    ReasoningPart,
    StopEvent,
    TextPart,
    ToolResultPart,
)
from tests.model_samples import BROKEN_TOOL_CALL, REQUEST, TOOL_CALL, TOOL_RESULT
from tests.openai_chat.fake_server import answering, client_for
from tests.openai_chat.wire_text import DONE, chunk, own_reasoning
from tests.streams import collect

SMALL = ModelRequest(
    system=("Be brief.", "Today is a made-up day."),
    messages=(Message(role="user", parts=(TextPart(text="Is the café open?"),)),),
    tools=(),
    max_output=64,
    cache_hints=(),
    deadline_ms=1_000,
    retries=0,
)


def body(request: ModelRequest) -> Any:
    return json.loads(render_request("made-up-local", request))


def with_parts(*parts: Any) -> ModelRequest:
    message = Message(role="assistant", parts=parts)
    return dataclasses.replace(SMALL, messages=(*SMALL.messages, message))


def test_a_small_request_becomes_exactly_these_bytes() -> None:
    expected = (
        '{"max_tokens":64,'
        '"messages":[{"content":"Be brief.\\n\\nToday is a made-up day.",'
        '"role":"system"},'
        '{"content":"Is the café open?","role":"user"}],'
        '"model":"made-up-local",'
        '"stream":true,'
        '"stream_options":{"include_usage":true}}'
    )
    assert render_request("made-up-local", SMALL) == expected.encode("utf-8")


def test_no_sampling_choice_or_format_field_is_sent() -> None:
    sent = body(REQUEST)
    assert set(sent) == {
        "max_tokens",
        "messages",
        "model",
        "stream",
        "stream_options",
        "tools",
    }


def test_tools_go_as_functions_in_order() -> None:
    [tool] = body(REQUEST)["tools"]
    assert tool == {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the first lines of a file.",
            "parameters": REQUEST.tools[0].arguments_schema,
        },
    }


def test_tool_results_come_before_the_users_text() -> None:
    second = ToolResultPart(call_id="call_2", text="Not found.", is_error=True)
    message = Message(
        role="user", parts=(TextPart(text="Also this."), TOOL_RESULT, second)
    )
    sent = body(dataclasses.replace(SMALL, messages=(message,)))["messages"][1:]
    assert sent == [
        {"role": "tool", "tool_call_id": "call_1", "content": "milk\neggs\ntea"},
        {"role": "tool", "tool_call_id": "call_2", "content": "Not found."},
        {"role": "user", "content": "Also this."},
    ]


def test_an_assistant_turn_joins_its_text_and_lists_its_calls() -> None:
    sent = body(with_parts(TextPart(text="I will "), TextPart(text="look."), TOOL_CALL))
    assert sent["messages"][-1] == {
        "role": "assistant",
        "content": "I will look.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"lines": 3, "path": "notes.txt"}',
                },
            }
        ],
    }


def test_arguments_that_did_not_parse_go_back_as_an_empty_object() -> None:
    sent = body(with_parts(BROKEN_TOOL_CALL))
    [call] = sent["messages"][-1]["tool_calls"]
    assert call["function"]["arguments"] == "{}"


def test_reasoning_goes_back_in_the_field_it_came_in() -> None:
    for dialect in ("llama_cpp", "vllm"):
        part = own_reasoning(dialect)
        sent = body(with_parts(part, TextPart(text="Yes.")))["messages"][-1]
        assert sent[str(part.opaque["field"])] == part.text


def test_reasoning_of_another_adapter_is_dropped() -> None:
    foreign = ReasoningPart(text="Sealed.", made_by="anthropic", opaque={"s": "x"})
    sent = body(with_parts(foreign, TextPart(text="Yes.")))["messages"][-1]
    assert sent == {"role": "assistant", "content": "Yes."}


def test_cache_hints_are_not_sent() -> None:
    hinted = dataclasses.replace(
        SMALL, cache_hints=(CacheHint(after="message", index=0),)
    )
    serve, server = answering(
        "llama_cpp", httpx.Response(200, content=chunk({}, "stop") + DONE)
    )
    collect(client_for(serve, "llama_cpp"), hinted)
    assert server.requests[0].content == render_request("made-up-local", SMALL)


def test_a_session_with_a_call_cut_for_length_goes_on() -> None:
    # llama.cpp answers 500 to every request whose history holds arguments
    # that are not JSON, so sending the cut text back would end the session.
    cut = dataclasses.replace(BROKEN_TOOL_CALL, id="call_9")
    result = ToolResultPart(call_id="call_9", text="Did not parse.", is_error=True)
    request = dataclasses.replace(
        SMALL,
        messages=(
            *SMALL.messages,
            Message(role="assistant", parts=(cut,)),
            Message(role="user", parts=(result,)),
        ),
    )
    reply = chunk({"content": "Ok."}) + chunk({}, "stop") + DONE
    serve, server = answering("llama_cpp", httpx.Response(200, content=reply))
    events = collect(client_for(serve, "llama_cpp"), request)
    assert isinstance(events[-1], StopEvent)
    assert fold_reply(events).message.parts == (TextPart(text="Ok."),)
    assert len(server.requests) == 1


def test_the_made_up_llama_server_refuses_arguments_that_are_not_json() -> None:
    # The test above means something only if the made-up server refuses as
    # the real one does.
    sent = json.loads(render_request("made-up-local", SMALL))
    sent["messages"].append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "no'},
                }
            ],
        }
    )
    serve, _ = answering("llama_cpp")
    request = httpx.Request(
        "POST", "http://127.0.0.1:9931/v1/chat/completions", json=sent
    )
    assert asyncio.run(serve(request)).status_code == 500
