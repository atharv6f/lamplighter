"""The one value every port uses to report an expected failure."""

from dataclasses import dataclass, field
from typing import Literal

from lamplighter.ports.json_value import JsonObject, JsonValue

type FailureKind = Literal[
    "transient",
    "throttled",
    "timeout",
    "unavailable",
    "invalid_input",
    "too_large",
    "denied",
    "needs_user",
    "conflict",
    "cancelled",
    "fatal",
]

type FailureEffect = Literal["none", "unknown", "done"]


@dataclass(frozen=True, slots=True)
class Failure:
    """An expected failure, returned as a value and never raised.

    Callers branch on `kind` and `effect` and on nothing else. `effect` says
    whether the action may have happened anyway. `message` is one line a person
    can read, written by the adapter and never copied from a provider. `detail`
    is for logs only.
    """

    kind: FailureKind
    effect: FailureEffect
    message: str
    source: str
    retry_after_ms: int | None = None
    detail: JsonObject = field(default_factory=dict[str, JsonValue])
    cause: "Failure | None" = None
    v: int = 1

    def __post_init__(self) -> None:
        """Reject a wait time on any kind that is not a request to slow down."""
        if self.retry_after_ms is not None and self.kind != "throttled":
            raise ValueError("retry_after_ms is only allowed with kind 'throttled'")
