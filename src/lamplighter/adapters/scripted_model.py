"""A model client that plays a script, so tests need no real model."""

import json
import random
from collections.abc import AsyncGenerator, Iterator, Sequence
from dataclasses import dataclass

from lamplighter.ports import (
    ErrorEvent,
    Failure,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ReasoningEvent,
    ReasoningPart,
    StopEvent,
    StopReason,
    TextEvent,
    TextPart,
    ToolCallEvent,
    ToolCallPart,
    Usage,
    UsageEvent,
    check_request,
    to_plain,
)

type ReplyPart = TextPart | ToolCallPart | ReasoningPart


@dataclass(frozen=True, slots=True)
class ScriptedReply:
    """A reply that is played to its end.

    With no `usage`, the model reports counts worked out by its own rule.
    """

    parts: tuple[ReplyPart, ...]
    reason: StopReason
    usage: Usage | None = None


@dataclass(frozen=True, slots=True)
class ScriptedBreak:
    """A reply that fails once the given parts have been played."""

    parts: tuple[ReplyPart, ...]
    failure: Failure


class ScriptedModel:
    """Plays one scripted turn for each call to `stream`. Satisfies ModelClient.

    Text is cut into pieces whose sizes come from `rng`, so code that reads the
    stream cannot come to rely on where the cuts fall.
    """

    def __init__(
        self,
        profile: ModelProfile,
        turns: Sequence[ScriptedReply | ScriptedBreak],
        rng: random.Random,
    ) -> None:
        """Take the profile to report, the turns to play, and the source of cuts."""
        self._profile = profile
        self._turns = iter(turns)
        self._rng = rng

    @property
    def profile(self) -> ModelProfile:
        """The profile given when this model was built."""
        return self._profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Play the next turn of the script as events."""
        refusal = check_request(self._profile, request)
        if refusal is not None:
            yield ErrorEvent(failure=refusal, model_reported=None, skipped=0)
            return
        turn = next(self._turns, None)
        if turn is None:
            yield self._error(self._script_is_over())
            return
        for event in self._content(turn.parts):
            yield event
        if isinstance(turn, ScriptedBreak):
            yield self._error(turn.failure)
            return
        yield UsageEvent(usage=turn.usage or _own_usage(request, turn))
        yield StopEvent(
            reason=turn.reason,
            provider_reason=turn.reason,
            model_reported=self._profile.model,
            skipped=0,
        )

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        """Give the prompt size by this model's own rule, which is exact for it."""
        return check_request(self._profile, request) or _count(request)

    def _content(self, parts: Sequence[ReplyPart]) -> Iterator[ModelEvent]:
        # A text part with no text gives no event, so it takes no place in the
        # reply. Counting it would leave a gap in the indexes.
        given = [part for part in parts if part != TextPart(text="")]
        for index, part in enumerate(given):
            match part:
                case TextPart():
                    for piece in self._pieces(part.text):
                        yield TextEvent(index=index, text=piece)
                case ToolCallPart():
                    yield ToolCallEvent(index=index, part=part)
                case ReasoningPart():
                    yield ReasoningEvent(index=index, part=part)

    def _pieces(self, text: str) -> Iterator[str]:
        start = 0
        while start < len(text):
            size = self._rng.randint(1, 12)
            yield text[start : start + size]
            start += size

    def _error(self, failure: Failure) -> ErrorEvent:
        return ErrorEvent(
            failure=failure, model_reported=self._profile.model, skipped=0
        )

    def _script_is_over(self) -> Failure:
        return Failure(
            kind="fatal",
            effect="none",
            message="The script has no turn left for this call.",
            source=f"ModelClient/{self._profile.adapter}",
        )


def _own_usage(request: ModelRequest, turn: ScriptedReply) -> Usage:
    return Usage(
        input_uncached=_count(request),
        cache_read=0,
        cache_write=0,
        output=_count(turn.parts),
    )


def _count(value: object) -> int:
    # One token for every four characters of the JSON form, rounded up. The
    # rule only has to be fixed, so a count and a reported usage always agree.
    return (len(json.dumps(to_plain(value))) + 3) // 4
