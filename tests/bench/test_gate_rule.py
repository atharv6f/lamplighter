"""The gate's rule is read with its checks and named by a hash of its values."""

import dataclasses
import tomllib

import pytest

from lamplighter.bench import GateGuards, GateRule, gate_rule, rule_hash
from lamplighter.ports import Failure, JsonValue

RULE_TEXT = """\
# What counts as better, fixed before any trial of an experiment runs.
metric = "pass_fraction_per_task"
method = "paired_sign_flip_mid_p"
# 50 in a thousand: a change that does nothing is kept about once in twenty.
alpha_permille = 50
trials_per_task = 3
# More infra trials than this, in either run set, and the answer is inconclusive.
infra_limit_permille = 50

[guards]
# The candidate's cost for each valid trial, as a share of the baseline's.
cost_per_valid_trial_permille = 1250
"""
# The same values, with no comments and in another order.
SHUFFLED_TEXT = """\
infra_limit_permille = 50
trials_per_task = 3
method = "paired_sign_flip_mid_p"
alpha_permille = 50
metric = "pass_fraction_per_task"
[guards]
cost_per_valid_trial_permille = 1250
"""
SPEC_HASH = "914fe7ad09f9cc3cf30f2a815cbc4aa826bc2f658c2240a5bf3880ec72574f32"


def _plain(text: str) -> JsonValue:
    return tomllib.loads(text)


def _rule(text: str) -> GateRule:
    rule = gate_rule(_plain(text))
    assert isinstance(rule, GateRule)
    return rule


def _changed(**values: JsonValue) -> JsonValue:
    plain = tomllib.loads(RULE_TEXT)
    plain.update(values)
    return plain


def test_the_rule_of_the_spec_reads_and_gives_its_printed_hash() -> None:
    rule = _rule(RULE_TEXT)
    assert rule == GateRule(
        metric="pass_fraction_per_task",
        method="paired_sign_flip_mid_p",
        alpha_permille=50,
        trials_per_task=3,
        infra_limit_permille=50,
        guards=GateGuards(cost_per_valid_trial_permille=1250),
    )
    assert rule_hash(rule) == SPEC_HASH


def test_comments_and_the_order_of_lines_do_not_change_the_hash() -> None:
    assert rule_hash(_rule(SHUFFLED_TEXT)) == SPEC_HASH


@pytest.mark.parametrize(
    "change",
    [
        {"alpha_permille": 49},
        {"trials_per_task": 2},
        {"infra_limit_permille": 51},
        {"guards": {"cost_per_valid_trial_permille": 1251}},
    ],
)
def test_each_value_changed_is_a_new_hash(change: dict[str, JsonValue]) -> None:
    rule = gate_rule(_changed(**change))
    assert isinstance(rule, GateRule)
    assert rule_hash(rule) != SPEC_HASH


@pytest.mark.parametrize(
    "change",
    [
        {"metric": "mean_reward"},
        {"method": "paired_sign_flip"},
        {"alpha_permille": 0},
        {"alpha_permille": 500},
        {"trials_per_task": 0},
        {"trials_per_task": 4},
        {"infra_limit_permille": -1},
        {"infra_limit_permille": 1001},
        {"guards": {"cost_per_valid_trial_permille": 0}},
        {"guards": {"cost_per_valid_trial_permille": 1250, "steps_permille": 1}},
        {"guards": {}},
        {"seed": 1},
        {"alpha_permille": True},
        {"alpha_permille": 50.0},
        {"trials_per_task": "3"},
    ],
)
def test_a_value_out_of_range_or_a_key_the_gate_does_not_read_is_refused(
    change: dict[str, JsonValue],
) -> None:
    refused = gate_rule(_changed(**change))
    assert isinstance(refused, Failure)
    assert refused.kind == "invalid_input"


@pytest.mark.parametrize(
    "key",
    [
        "metric",
        "method",
        "alpha_permille",
        "trials_per_task",
        "infra_limit_permille",
        "guards",
    ],
)
def test_a_rule_with_a_value_missing_is_refused(key: str) -> None:
    plain = tomllib.loads(RULE_TEXT)
    del plain[key]
    refused = gate_rule(plain)
    assert isinstance(refused, Failure)
    assert refused.kind == "invalid_input"


@pytest.mark.parametrize(
    ("key", "edge"),
    [
        ("alpha_permille", 1),
        ("alpha_permille", 499),
        ("trials_per_task", 1),
        ("infra_limit_permille", 0),
        ("infra_limit_permille", 1000),
    ],
)
def test_the_edges_of_each_range_are_allowed(key: str, edge: int) -> None:
    rule = gate_rule(_changed(**{key: edge}))
    assert isinstance(rule, GateRule)
    assert dataclasses.asdict(rule)[key] == edge


def test_a_cost_guard_of_one_is_allowed() -> None:
    rule = gate_rule(_changed(guards={"cost_per_valid_trial_permille": 1}))
    assert isinstance(rule, GateRule)
