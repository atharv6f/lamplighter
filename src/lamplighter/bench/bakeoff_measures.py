"""What a bake-off row holds of one candidate's measures, as plain records."""

from dataclasses import dataclass
from typing import Literal

from lamplighter.ports import JsonObject, Verdict

# The hard requirements, by the names a candidate row's `hard` gives them.
HARD = (
    "broken_call_visible",
    "prompt_over_window_caught",
    "loopback_only",
    "pinned",
)

type ReplyReason = Literal[
    "usable",
    "dropped_calls",
    "call_markup",
    "unparsed",
    "unknown_tool",
    "schema",
    "no_call",
    "empty",
    "cut",
    "too_large",
    "error",
]
type AdapterPerRequest = Literal["yes", "no", "at_start"]


@dataclass(frozen=True, slots=True)
class UsableReplies:
    """Each captured request's reply, judged, in the capture set's order.

    `total` is every request of the set, so a request refused as too
    large counts against the share. `calls` and `good_calls` count the
    tool calls the replies made, and those that named a tool of their
    request, parsed, and fit its schema.
    """

    usable: int
    total: int
    each: tuple[ReplyReason, ...]
    calls: int
    good_calls: int


@dataclass(frozen=True, slots=True)
class FirstToken:
    """The warm time to the first delta, five times, and their median."""

    median_ms: int
    each_ms: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Speeds:
    """The medians of five requests at each size, in thousandths of a token a second.

    Prompt speed is prompt tokens over the time to the first delta, and
    generation speed completion tokens over the time from the first delta
    to the last. `server_timings` holds what the server says of its own
    speed, where it says it.
    """

    prompt_8k_milli: int
    generation_8k_milli: int
    prompt_32k_milli: int
    generation_32k_milli: int
    server_timings: JsonObject


@dataclass(frozen=True, slots=True)
class PeakMemory:
    """The peaks seen while the candidate answered.

    `added_bytes` is memory the footprint does not show and the server
    printed at load, such as a KV pool, so the peak is
    `footprint_bytes + added_bytes`. `longest_gap_ms` is the longest time
    between two reads of the footprint.
    """

    footprint_bytes: int
    added_bytes: int
    pressure_level: int
    reads: int
    longest_gap_ms: int


@dataclass(frozen=True, slots=True)
class CandidateMeasures:
    """Every criterion of a candidate row, by the names the row gives them."""

    usable_replies: UsableReplies
    first_token_ms: FirstToken
    tokens_per_second: Speeds
    peak_memory: PeakMemory
    adapter_per_request: AdapterPerRequest
    adapter_evidence: str
    forced_schema: Verdict


@dataclass(frozen=True, slots=True)
class SoakCounts:
    """What a soak saw. `whole` is false for a soak that was killed part way."""

    whole: bool
    requests: int
    failed: int
    first_hour_peak_bytes: int
    end_bytes: int
    first_hour_median_ms: int
    last_hour_median_ms: int


@dataclass(frozen=True, slots=True)
class SoakMeasures:
    """A soak row's `stays_up`: whether it passed, and what it saw."""

    passed: bool
    counts: SoakCounts
