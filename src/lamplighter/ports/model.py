"""The port the core calls for a model's reply, and the records that cross it."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject


@dataclass(frozen=True, slots=True)
class TextPart:
    """Text in a message, from either side."""

    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    """The model's request to run one tool.

    When the model's arguments did not parse, `arguments` is None,
    `raw_arguments` holds the text as received and `parse_error` says why.
    """

    id: str
    name: str
    arguments: JsonObject | None
    raw_arguments: str | None = None
    parse_error: str | None = None
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    """What a tool gave back for the call with the id `call_id`."""

    call_id: str
    text: str
    is_error: bool
    kind: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True, slots=True)
class ReasoningPart:
    """The model's own working, sent back so the model keeps its thread.

    `opaque` belongs to the adapter named in `made_by`. Everything else stores
    it and never reads it.
    """

    text: str
    made_by: str
    opaque: JsonObject
    kind: Literal["reasoning"] = "reasoning"


type Part = TextPart | ToolCallPart | ToolResultPart | ReasoningPart


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of a conversation."""

    role: Literal["user", "assistant"]
    parts: tuple[Part, ...]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """What the model is told about one tool."""

    name: str
    description: str
    arguments_schema: JsonObject


@dataclass(frozen=True, slots=True)
class CacheHint:
    """Marks a point in a request. What comes before it is likely to be sent again."""

    after: Literal["system", "message"]
    index: int


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Everything one call to a model needs.

    Every tool call in `messages` already has its result. `deadline_ms` is how
    long the whole call may take, with retries and waits included. `retries`
    is how many more attempts may be made after the first.
    """

    system: tuple[str, ...]
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output: int
    cache_hints: tuple[CacheHint, ...]
    deadline_ms: int
    retries: int


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for one call.

    The four counts never overlap, so the prompt size is the sum of the first
    three. None means the server did not say. It never means zero.
    """

    input_uncached: int | None
    cache_read: int | None
    cache_write: int | None
    output: int | None


type StopReason = Literal["end", "tool_calls", "max_output", "refused", "other"]


@dataclass(frozen=True, slots=True)
class TextEvent:
    """One piece of the text part at `index`. Pieces of one part join in order."""

    index: int
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ReasoningEvent:
    """A whole reasoning part, at place `index` in the reply."""

    index: int
    part: ReasoningPart
    kind: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """A whole tool call, at place `index` in the reply."""

    index: int
    part: ToolCallPart
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """The token counts for the call."""

    usage: Usage
    kind: Literal["usage"] = "usage"


@dataclass(frozen=True, slots=True)
class StopEvent:
    """The reply is complete.

    `provider_reason` keeps the provider's own word for the log.
    `model_reported` is the model name the server gave. `skipped` counts the
    wire events the adapter did not know and passed over.
    """

    reason: StopReason
    provider_reason: str
    model_reported: str | None
    skipped: int
    kind: Literal["stop"] = "stop"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """The call failed. The other two fields mean what they do on a StopEvent."""

    failure: Failure
    model_reported: str | None
    skipped: int
    kind: Literal["error"] = "error"


type ModelEvent = (
    TextEvent | ReasoningEvent | ToolCallEvent | UsageEvent | StopEvent | ErrorEvent
)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """What one model on one server can do, so callers ask and never guess.

    `cache_hints` is how many hints a request may carry, and 0 means hints are
    ignored. `count_error_permille` is how far `count_tokens` may be from the
    real prompt size, in thousandths of that size. `window` is the largest
    prompt plus output the model takes, in tokens. `concurrent_requests` is
    how many requests the server runs at once. `stays_on_mac` says every
    byte of a call goes only to this Mac. The settings loader works it out
    from the adapter and its address, and never reads it from a setting.
    """

    adapter: str
    model: str
    max_output: int
    tool_calls: bool
    cache_hints: int
    count_error_permille: int
    window: int
    concurrent_requests: int
    stays_on_mac: bool


class ModelClient(Protocol):
    """One model on one server, behind one shape.

    Callers can rely on this:

    - `stream` yields text pieces, whole reasoning parts and whole tool calls
      in the order the model produced them, then at most one UsageEvent, then
      exactly one StopEvent or ErrorEvent. Nothing follows that last event.
    - Each tool call arrives once, with an id that is unique in the reply.
    - The `index` on an event is the place of its part in the reply. It starts
      at 0 and has no gaps.
    - An expected failure is never raised. `stream` ends with an ErrorEvent and
      `count_tokens` returns the Failure. The effect of a Failure is always
      "none", because a model call changes nothing in the world.
    - A request that asks for something the profile lacks fails with kind
      "invalid_input" before any network call, from both methods.
    - The same request always becomes the same bytes on the wire.
    - A retry happens only before the first event has been yielded.
    - Parts taken from a reply are accepted unchanged in the next request.
    - Closing the stream ends the call. Nothing more is yielded.
    """

    @property
    def profile(self) -> ModelProfile:
        """What this model and server can do."""
        ...

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Send the request and yield the reply as events."""
        ...

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        """Give the prompt size in tokens, within the profile's stated error."""
        ...
