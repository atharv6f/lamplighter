"""Made-up records, one of every kind that crosses the model port."""

from lamplighter.ports import (
    CacheHint,
    ErrorEvent,
    Failure,
    Message,
    ModelProfile,
    ModelRequest,
    ReasoningEvent,
    ReasoningPart,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    UsageEvent,
)

TEXT = TextPart(text="The kettle is on.")
TOOL_CALL = ToolCallPart(
    id="call_1", name="read_file", arguments={"path": "notes.txt", "lines": 3}
)
BROKEN_TOOL_CALL = ToolCallPart(
    id="call_2",
    name="read_file",
    arguments=None,
    raw_arguments='{"path": "no',
    parse_error="the text ends inside a string",
)
TOOL_RESULT = ToolResultPart(call_id="call_1", text="milk\neggs\ntea", is_error=False)
REASONING = ReasoningPart(
    text="",
    made_by="scripted",
    opaque={"sealed": "c2VhbGVk", "ratio": 0.5, "tags": ["a", None, True]},
)

TOOL = ToolSpec(
    name="read_file",
    description="Read the first lines of a file.",
    arguments_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "lines": {"type": "integer"}},
        "required": ["path"],
    },
)

REQUEST = ModelRequest(
    system=("You are a careful helper.", "Today is a made-up day."),
    messages=(
        Message(role="user", parts=(TextPart(text="What is on my list?"),)),
        Message(role="assistant", parts=(REASONING, TEXT, TOOL_CALL, BROKEN_TOOL_CALL)),
        Message(role="user", parts=(TOOL_RESULT,)),
    ),
    tools=(TOOL,),
    max_output=1024,
    cache_hints=(
        CacheHint(after="system", index=1),
        CacheHint(after="message", index=2),
    ),
    deadline_ms=30_000,
    retries=2,
)

USAGE = Usage(input_uncached=500, cache_read=38_000, cache_write=1_500, output=800)
PARTIAL_USAGE = Usage(
    input_uncached=500, cache_read=None, cache_write=None, output=None
)

THROTTLED = Failure(
    kind="throttled",
    effect="none",
    message="The model server asked for a slower pace.",
    source="ModelClient/scripted",
    retry_after_ms=2_000,
    detail={"status": 429, "request_id": "req_made_up"},
    cause=Failure(
        kind="transient",
        effect="none",
        message="The connection dropped.",
        source="ModelClient/scripted",
    ),
)

EVENTS = (
    ReasoningEvent(index=0, part=REASONING),
    TextEvent(index=1, text="The kettle "),
    TextEvent(index=1, text="is on."),
    ToolCallEvent(index=2, part=TOOL_CALL),
    UsageEvent(usage=USAGE),
    UsageEvent(usage=PARTIAL_USAGE),
    StopEvent(
        reason="tool_calls",
        provider_reason="tool_use",
        model_reported="made-up-1",
        skipped=0,
    ),
    ErrorEvent(failure=THROTTLED, model_reported=None, skipped=1),
)

PROFILE = ModelProfile(
    adapter="scripted",
    model="made-up-1",
    max_output=2_000,
    tool_calls=True,
    cache_hints=0,
    count_error_permille=0,
)

RECORDS: tuple[object, ...] = (
    TEXT,
    TOOL_CALL,
    BROKEN_TOOL_CALL,
    TOOL_RESULT,
    REASONING,
    TOOL,
    REQUEST,
    USAGE,
    PARTIAL_USAGE,
    THROTTLED,
    PROFILE,
    *EVENTS,
)
