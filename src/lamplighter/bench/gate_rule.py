"""The gate's rule, read from plain values and named by its hash."""

import hashlib
from dataclasses import dataclass
from typing import Literal

from lamplighter.ports import Failure, JsonValue, canonical_json, from_plain

SOURCE = "gate_rule"


@dataclass(frozen=True, slots=True)
class GateGuards:
    """What the candidate may not do worse, whatever its passes.

    `cost_per_valid_trial_permille` is the candidate's cost for each valid
    trial as a share of the baseline's, in thousandths.
    """

    cost_per_valid_trial_permille: int


@dataclass(frozen=True, slots=True)
class GateRule:
    """What counts as better, fixed before any trial of an experiment runs."""

    metric: Literal["pass_fraction_per_task"]
    method: Literal["paired_sign_flip_mid_p"]
    alpha_permille: int
    trials_per_task: int
    infra_limit_permille: int
    guards: GateGuards


def gate_rule(plain: JsonValue) -> GateRule | Failure:
    """Read a rule, refusing any key it does not have and any value out of range.

    An alpha of 500 or more is refused: the two p-values add up to one, so
    at 500 both could be at most alpha, and a run set compared with itself
    would be kept. A task's pass fraction is counted in sixths, which holds
    1, 2 or 3 trials and no more.
    """
    try:
        rule = from_plain(GateRule, plain)
    except ValueError as error:
        return _refused(f"The gate rule does not have the expected form: {error}.")
    if not 1 <= rule.alpha_permille <= 499:
        return _refused("The gate rule's alpha_permille must be 1 to 499.")
    if rule.trials_per_task not in (1, 2, 3):
        return _refused("The gate rule's trials_per_task must be 1, 2 or 3.")
    if not 0 <= rule.infra_limit_permille <= 1000:
        return _refused("The gate rule's infra_limit_permille must be 0 to 1000.")
    if rule.guards.cost_per_valid_trial_permille < 1:
        return _refused("The gate rule's cost guard must be 1 or more.")
    return rule


def rule_hash(rule: GateRule) -> str:
    """Give the SHA-256 of the rule's canonical JSON, the name it decides under."""
    return hashlib.sha256(canonical_json(rule).encode("utf-8")).hexdigest()


def _refused(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=SOURCE)
