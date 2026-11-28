"""The record a trial leaves, and the rule that says whether it tested the agent."""

from dataclasses import dataclass
from typing import Literal, get_args

from lamplighter.ports import JsonValue, canonical_json, from_plain, read_saved_json

type InfraReason = Literal[
    "budget_reached",
    "config_mismatch",
    "executor_unavailable",
    "harness_failure",
    "model_transient",
    "model_throttled",
    "model_timeout",
    "model_unavailable",
    "model_denied",
    "model_needs_user",
    "model_conflict",
    "model_cancelled",
    "model_fatal",
    "sweep_failed",
    "export_failed",
]
type BoundWord = Literal["too_large", "too_many_entries", "too_deep", "unreadable"]

# The ends of a failed run that are the agent's doing: a config whose agent
# repeats itself or sends a request the provider refuses is a worse config.
_AGENTS_FAILURES = frozenset(
    {
        "repeated_call",
        "refused",
        "empty_reply",
        "model_invalid_input",
        "model_too_large",
    }
)
# The model's failures that are an outage and not the agent's doing, each
# the reason of its own name.
_MODEL_OUTAGES: dict[str, InfraReason] = {
    reason: reason
    for reason in get_args(InfraReason.__value__)
    if reason.startswith("model_")
}


# The fields added to the record after its first form, as a record of that
# form reads.
_ADDED: dict[str, JsonValue] = {"models_called": [], "escalated": False}
# A run ended by a reply the adapter's end checks refused, such as one with
# call markup in its text, is the agent's fail: a server that garbles calls
# must lower its config's pass rate, not leave the trial out as an outage.
OUTPUT_FAULT = "model_output:"


@dataclass(frozen=True, slots=True)
class Tokens:
    """The four token counts of a run, added up over its replies."""

    uncached: int
    read: int
    written: int
    output: int


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """What one trial did, written last in the trial's own folder.

    The two config ids are None when the trial stopped before its tools
    were built. `infra_reason` is set exactly when `gradable` is false, and
    `left_out_of_bounds` only ever on a gradable trial. `models_called` and
    `escalated` are the run summary's; a record written before they were
    kept reads as no model named and no escalation.
    """

    portable_config_id: str | None
    config_id: str | None
    code_revision: str
    model: str
    provider: str
    session_id: str
    run_id: str | None
    run_reason: str | None
    run_detail: str | None
    gradable: bool
    infra_reason: InfraReason | None
    left_out_of_bounds: BoundWord | None
    steps: int
    tool_calls: int
    tokens: Tokens
    micro_dollars: int | None
    ms: int
    exported_events: int | None
    models_called: tuple[str, ...] = ()
    escalated: bool = False
    v: int = 1


@dataclass(frozen=True, slots=True)
class TrialEnds:
    """How each part of a trial after its start went, as plain values.

    `run_reason` and `run_detail` are None when the loop gave a failure or
    ended no run.
    """

    run_reason: str | None
    run_detail: str | None
    command_unavailable: bool
    swept: bool
    exported: bool


def infra_reason(ends: TrialEnds) -> InfraReason | None:
    """Give why the trial did not test the agent, or None when it did.

    The rule never looks at whether the check would pass. When more than
    one part went wrong, the first in this order is the reason: the run,
    a command that lost the container, the sweep, the export.
    """
    if ends.run_reason is None:
        return "harness_failure"
    run = _run_reason(ends.run_reason, ends.run_detail)
    if run is not None:
        return run
    if ends.command_unavailable:
        return "executor_unavailable"
    if not ends.swept:
        return "sweep_failed"
    if not ends.exported:
        return "export_failed"
    return None


def record_text(record: TrialRecord) -> str:
    """Give the text a trial record is saved as."""
    return canonical_json(record)


def read_record(text: str) -> TrialRecord | None:
    """Give the record a text holds, or None when it is not a whole record.

    A text that is not JSON, lacks a field, has one of the wrong type, a
    count below 0, or a reason that does not fit `gradable`, is not one.
    """
    try:
        plain = read_saved_json(text)
        record = from_plain(
            TrialRecord, {**_ADDED, **plain} if isinstance(plain, dict) else plain
        )
    except ValueError:
        return None
    if record.v != 1 or (record.infra_reason is None) != record.gradable:
        return None
    if record.left_out_of_bounds is not None and not record.gradable:
        return None
    counts = (
        record.steps,
        record.tool_calls,
        record.ms,
        record.micro_dollars or 0,
        record.exported_events or 0,
        *(record.tokens.uncached, record.tokens.read),
        *(record.tokens.written, record.tokens.output),
    )
    return record if min(counts) >= 0 else None


def _run_reason(reason: str, detail: str | None) -> InfraReason | None:
    if reason in ("done", "limit"):
        return None
    if reason == "failed" and detail in _AGENTS_FAILURES:
        return None
    if reason == "failed" and detail is not None and detail.startswith(OUTPUT_FAULT):
        return None
    if reason == "failed" and detail in _MODEL_OUTAGES:
        return _MODEL_OUTAGES[detail]
    # A cancel, a changed config, and any end no rule names: none of it is
    # the agent's doing.
    return "harness_failure"
