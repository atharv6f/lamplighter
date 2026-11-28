"""The rule from a trial's record and how its check ended to pass, fail or infra."""

from dataclasses import dataclass
from typing import Literal

from lamplighter.bench.trial_record import BoundWord, InfraReason, TrialRecord
from lamplighter.ports import CheckEnd

type OutcomeKind = Literal["pass", "fail", "infra"]
type OutcomeReason = (
    InfraReason
    | BoundWord
    | Literal[
        "no_record", "no_check_result", "check_timed_out", "check_unreadable_reward"
    ]
)


@dataclass(frozen=True, slots=True)
class TrialOutcome:
    """Whether a trial passed, failed, or did not test the agent, and why.

    A pass has no reason, and neither has a fail on the check's own reward.
    """

    kind: OutcomeKind
    reason: OutcomeReason | None


def harness_outcome(record: TrialRecord | None, check: CheckEnd) -> TrialOutcome:
    """Give the outcome of a trial of the harness.

    `record` is None when the trial left no record, or a file that is not
    one. The record is read before the check, so a check that passed on a
    trial that did not test the agent, or on what the agent left out of
    bounds, is no pass.
    """
    if record is None:
        return TrialOutcome("infra", "no_record")
    if record.infra_reason is not None:
        return TrialOutcome("infra", record.infra_reason)
    if record.left_out_of_bounds is not None:
        return TrialOutcome("fail", record.left_out_of_bounds)
    return check_outcome(check)


def check_outcome(check: CheckEnd) -> TrialOutcome:
    """Give the outcome from how the check ended alone, as for a baseline harness.

    A check that ran out of its time or left no reward that can be read
    ran on what the agent left, so it is a fail. Only a check with no
    result at all did not test the agent.
    """
    match check:
        case "passed":
            return TrialOutcome("pass", None)
        case "failed":
            return TrialOutcome("fail", None)
        case "timed_out":
            return TrialOutcome("fail", "check_timed_out")
        case "unreadable_reward":
            return TrialOutcome("fail", "check_unreadable_reward")
        case "none":
            return TrialOutcome("infra", "no_check_result")
