"""The capture set: requests the loop really sent, rebuilt, chosen, one a line.

Every function here is pure. Where the runs come from, and how each
request is counted, is the caller's.
"""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.core import MODEL_REQUEST, MODEL_RESPONSE, Reply, rebuild_messages
from lamplighter.ports import (
    Event,
    Failure,
    ModelRequest,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    from_plain,
    from_stored,
    read_saved_json,
    saved_json,
)

SET_SIZE = 200
MOST_FROM_ONE_RUN = 10

type Source = Literal["chat", "trial"]


@dataclass(frozen=True, slots=True)
class Rebuild:
    """What a request takes from the settings it ran under, beside its messages."""

    system: str
    tools: tuple[ToolSpec, ...]
    max_output: int
    deadline_ms: int
    retries: int


@dataclass(frozen=True, slots=True)
class Captured:
    """One rebuilt request, and whether the reply recorded for it made a tool call."""

    request: ModelRequest
    recorded_call: bool
    source: Source


@dataclass(frozen=True, slots=True)
class CapturedRun:
    """The requests of one run, in the order the loop sent them."""

    source: Source
    requests: tuple[Captured, ...]


@dataclass(frozen=True, slots=True)
class FirstPass:
    """The runs left after steps 4 to 6, and how many requests each step passed over."""

    runs: tuple[CapturedRun, ...]
    held_out: int
    repeats: int
    over_the_cap: int


@dataclass(frozen=True, slots=True)
class Chosen:
    """The capture set's requests in order, and how many were too large for a model."""

    requests: tuple[Captured, ...]
    too_large: int


def run_requests(
    events: Sequence[Event], run_id: str, rebuild: Rebuild, source: Source
) -> CapturedRun | Failure:
    """Rebuild each request of the run `run_id`, as the loop built it.

    The messages are those rebuilt from every event of the session before
    the request, and the rest is from the settings. A request whose reply
    was never saved is left out, because there is nothing to compare its
    new reply with.
    """
    found: list[Captured] = []
    for place, event in enumerate(events):
        if event.type != MODEL_REQUEST or event.run_id != run_id:
            continue
        reply = _reply_after(events[place + 1 :], run_id)
        if reply is None:
            continue
        messages = rebuild_messages(events[:place])
        if isinstance(messages, Failure):
            return messages
        request = ModelRequest(
            system=(rebuild.system,),
            messages=messages,
            tools=rebuild.tools,
            max_output=rebuild.max_output,
            cache_hints=(),
            deadline_ms=rebuild.deadline_ms,
            retries=rebuild.retries,
        )
        called = any(isinstance(p, ToolCallPart) for p in reply.message.parts)
        found.append(Captured(request, called, source))
    return CapturedRun(source, tuple(found))


def first_pass(runs: Sequence[CapturedRun], held_out: Sequence[str]) -> FirstPass:
    """Apply steps 4 to 6 to the runs, in their order.

    Step 4 passes over a chat request whose text holds the instruction of
    a held-out task, compared after each run of spaces is made one space.
    Step 5 keeps only the first of requests with the same bytes. Step 6
    keeps the first 10 left of each run. A run left with none is dropped.
    """
    texts = [_spaced(text) for text in held_out if _spaced(text)]
    seen: set[str] = set()
    kept: list[CapturedRun] = []
    held = repeats = over = 0
    for run in runs:
        left: list[Captured] = []
        for captured in run.requests:
            if captured.source == "chat" and _holds(captured.request, texts):
                held += 1
                continue
            sha = request_hash(captured.request)
            if sha in seen:
                repeats += 1
                continue
            seen.add(sha)
            left.append(captured)
        over += max(0, len(left) - MOST_FROM_ONE_RUN)
        if left:
            kept.append(CapturedRun(run.source, tuple(left[:MOST_FROM_ONE_RUN])))
    return FirstPass(tuple(kept), held, repeats, over)


def chosen(
    runs: Sequence[CapturedRun],
    counts: Mapping[str, Sequence[int]],
    window: int,
    max_tokens: int,
) -> Chosen:
    """Apply steps 7 and 8: fit every count, then take runs in a fixed order.

    `counts` holds, by each request's hash, its size by each counting
    model. A request is left out when any count plus `max_tokens` is above
    `window`, so every candidate is asked the same requests and all fit.
    The runs left are ordered by the hash of their first request, and
    their requests are taken in order until there are 200, so a run's
    requests stay together as the loop sent them.
    """
    too_large = 0
    fitted: list[tuple[str, tuple[Captured, ...]]] = []
    for run in runs:
        fits = [
            c
            for c in run.requests
            if all(n + max_tokens <= window for n in counts[request_hash(c.request)])
        ]
        too_large += len(run.requests) - len(fits)
        if fits:
            fitted.append((request_hash(fits[0].request), tuple(fits)))
    taken = [c for _, fits in sorted(fitted, key=lambda run: run[0]) for c in fits]
    return Chosen(tuple(taken[:SET_SIZE]), too_large)


def request_hash(request: ModelRequest) -> str:
    """Give the SHA-256 of the request's saved bytes, which names it in the set."""
    return hashlib.sha256(saved_json(request).encode("utf-8")).hexdigest()


def capture_line(captured: Captured) -> str:
    """Give the one line a request is written as, without its newline."""
    return saved_json(captured)


def read_capture(text: str) -> tuple[Captured, ...]:
    """Give the requests of a capture file, in order.

    Raises ValueError for a line that is not one `capture_line` wrote.
    """
    return tuple(
        from_plain(Captured, read_saved_json(line)) for line in text.splitlines()
    )


def set_hash(text: str) -> str:
    """Give the capture set's hash: the SHA-256 of its file's bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reply_after(events: Sequence[Event], run_id: str) -> Reply | None:
    for event in events:
        if event.run_id != run_id:
            continue
        if event.type == MODEL_REQUEST:
            return None
        if event.type == MODEL_RESPONSE:
            return from_stored(Reply, event.payload)
    return None


def _spaced(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _holds(request: ModelRequest, texts: Sequence[str]) -> bool:
    if not texts:
        return False
    written = _spaced(
        " ".join(
            part.text
            for message in request.messages
            for part in message.parts
            if isinstance(part, TextPart | ToolResultPart)
        )
    )
    return any(text in written for text in texts)
