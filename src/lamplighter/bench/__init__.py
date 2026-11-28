"""The bench's own rules, as pure functions on plain values."""

from lamplighter.bench.outcome import (
    OutcomeKind,
    OutcomeReason,
    TrialOutcome,
    check_outcome,
    harness_outcome,
)
from lamplighter.bench.run_set import (
    HARNESS,
    NotWhole,
    RunSet,
    RunSetPlan,
    TaskCount,
    TrialResult,
    run_set,
)
from lamplighter.bench.trial_record import (
    BoundWord,
    InfraReason,
    Tokens,
    TrialEnds,
    TrialRecord,
    infra_reason,
    read_record,
    record_text,
)

__all__ = [
    "HARNESS",
    "BoundWord",
    "InfraReason",
    "NotWhole",
    "OutcomeKind",
    "OutcomeReason",
    "RunSet",
    "RunSetPlan",
    "TaskCount",
    "Tokens",
    "TrialEnds",
    "TrialOutcome",
    "TrialRecord",
    "TrialResult",
    "check_outcome",
    "harness_outcome",
    "infra_reason",
    "read_record",
    "record_text",
    "run_set",
]
