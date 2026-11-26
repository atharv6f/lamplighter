import dataclasses
import json
from typing import Any

from lamplighter.adapters.anthropic.request import (
    render_count_request,
    render_request,
)
from lamplighter.ports import (
    CacheHint,
    Message,
    ModelRequest,
    ReasoningPart,
    TextPart,
    ToolResultPart,
    ToolSpec,
)
from tests.model_samples import BROKEN_TOOL_CALL, REQUEST, TOOL, TOOL_CALL

SEALED = {"type": "thinking", "thinking": "", "signature": "bWFkZS11cA=="}
OWN_REASONING = ReasoningPart(text="", made_by="anthropic", opaque=SEALED)
FOREIGN_REASONING = ReasoningPart(text="", made_by="scripted", opaque={"sealed": "x"})

SMALL = ModelRequest(
    system=("Be brief.",),
    messages=(Message(role="user", parts=(TextPart(text="Is the café open?"),)),),
    tools=(),
    max_output=64,
    cache_hints=(),
    deadline_ms=1_000,
    retries=0,
)


def body(request: ModelRequest) -> Any:
    return json.loads(render_request("made-up-1", request))


def with_parts(*parts: Any) -> ModelRequest:
    message = Message(role="assistant", parts=parts)
    return dataclasses.replace(SMALL, messages=(*SMALL.messages, message))


def test_a_small_request_becomes_exactly_these_bytes() -> None:
    expected = (
        '{"max_tokens":64,'
        '"messages":[{"content":[{"text":"Is the café open?","type":"text"}],'
        '"role":"user"}],'
        '"model":"made-up-1",'
        '"stream":true,'
        '"system":[{"text":"Be brief.","type":"text"}]}'
    )
    assert render_request("made-up-1", SMALL) == expected.encode("utf-8")


def test_the_bytes_do_not_depend_on_the_order_keys_were_built_in() -> None:
    forward = ToolSpec(
        name="read_file",
        description="Read a file.",
        arguments_schema={"type": "object", "properties": {"a": {}, "b": {}}},
    )
    backward = ToolSpec(
        name="read_file",
        description="Read a file.",
        arguments_schema={"properties": {"b": {}, "a": {}}, "type": "object"},
    )
    one = render_request("made-up-1", dataclasses.replace(REQUEST, tools=(forward,)))
    two = render_request("made-up-1", dataclasses.replace(REQUEST, tools=(backward,)))
    assert one == two
    assert render_request("made-up-1", REQUEST) == render_request("made-up-1", REQUEST)


def test_tools_stay_in_the_order_the_request_gives_them() -> None:
    zebra = dataclasses.replace(TOOL, name="zebra")
    apple = dataclasses.replace(TOOL, name="apple")
    tools = body(dataclasses.replace(REQUEST, tools=(zebra, apple)))["tools"]
    assert [tool["name"] for tool in tools] == ["zebra", "apple"]
    assert tools[0] == {
        "name": "zebra",
        "description": TOOL.description,
        "input_schema": TOOL.arguments_schema,
    }


def test_an_empty_system_and_no_tools_are_left_out() -> None:
    bare = body(dataclasses.replace(SMALL, system=()))
    assert "system" not in bare
    assert "tools" not in bare


def test_the_output_limit_and_the_model_are_sent_and_the_reply_is_streamed() -> None:
    sent = body(REQUEST)
    assert sent["max_tokens"] == REQUEST.max_output
    assert sent["model"] == "made-up-1"
    assert sent["stream"] is True


def test_no_thinking_or_sampling_setting_is_sent() -> None:
    assert set(body(REQUEST)) == {
        "max_tokens",
        "messages",
        "model",
        "stream",
        "system",
        "tools",
    }


def test_a_tool_call_and_its_result_take_the_api_shapes() -> None:
    result = ToolResultPart(call_id=TOOL_CALL.id, text="milk", is_error=True)
    request = dataclasses.replace(
        with_parts(TOOL_CALL),
        messages=(
            *with_parts(TOOL_CALL).messages,
            Message(role="user", parts=(result,)),
        ),
    )
    messages = body(request)["messages"]
    assert messages[1] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": TOOL_CALL.id,
                "name": TOOL_CALL.name,
                "input": TOOL_CALL.arguments,
            }
        ],
    }
    assert messages[2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": TOOL_CALL.id,
            "content": "milk",
            "is_error": True,
        }
    ]


def test_a_tool_call_whose_arguments_did_not_parse_is_sent_with_no_arguments() -> None:
    block = body(with_parts(BROKEN_TOOL_CALL))["messages"][1]["content"][0]
    assert block["input"] == {}
    assert block["id"] == BROKEN_TOOL_CALL.id


def test_its_own_reasoning_goes_back_exactly_as_it_came() -> None:
    blocks = body(with_parts(OWN_REASONING, TextPart(text="Yes.")))["messages"][1]
    assert blocks["content"][0] == SEALED


def test_reasoning_made_by_another_adapter_is_left_out() -> None:
    blocks = body(with_parts(FOREIGN_REASONING, TextPart(text="Yes.")))["messages"][1]
    assert blocks["content"] == [{"type": "text", "text": "Yes."}]


def test_nothing_is_marked_for_the_cache_without_a_hint() -> None:
    plain = dataclasses.replace(REQUEST, cache_hints=())
    assert b"cache_control" not in render_request("made-up-1", plain)


def test_a_system_hint_marks_that_system_text_only() -> None:
    hinted = dataclasses.replace(REQUEST, cache_hints=(CacheHint("system", 1),))
    system = body(hinted)["system"]
    assert "cache_control" not in system[0]
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert b'"cache_control"' not in json.dumps(body(hinted)["messages"]).encode()


def test_a_message_hint_marks_the_last_block_of_that_message_only() -> None:
    hinted = dataclasses.replace(REQUEST, cache_hints=(CacheHint("message", 1),))
    sent = body(hinted)
    marked = [
        (m, b)
        for m, message in enumerate(sent["messages"])
        for b, block in enumerate(message["content"])
        if "cache_control" in block
    ]
    last_block = len(sent["messages"][1]["content"]) - 1
    assert marked == [(1, last_block)]
    assert "cache_control" not in json.dumps(sent["system"])


def test_a_mark_moves_back_past_reasoning_which_cannot_carry_one() -> None:
    request = with_parts(TextPart(text="Yes."), OWN_REASONING)
    hinted = dataclasses.replace(request, cache_hints=(CacheHint("message", 1),))
    content = body(hinted)["messages"][1]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1] == SEALED


def test_a_counting_body_is_the_prompt_and_nothing_about_the_reply() -> None:
    whole = body(REQUEST)
    counting = json.loads(render_count_request("made-up-1", REQUEST))
    assert set(whole) - set(counting) == {"max_tokens", "stream"}
    assert counting == {key: whole[key] for key in counting}


def test_a_small_counting_request_becomes_exactly_these_bytes() -> None:
    expected = (
        '{"messages":[{"content":[{"text":"Is the café open?","type":"text"}],'
        '"role":"user"}],'
        '"model":"made-up-1",'
        '"system":[{"text":"Be brief.","type":"text"}]}'
    )
    assert render_count_request("made-up-1", SMALL) == expected.encode("utf-8")
