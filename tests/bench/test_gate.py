"""The gate keeps its laws, decides the worked cases, and refuses what it cannot."""

import dataclasses
import os
import random
import subprocess
import sys

import pytest

from lamplighter.bench import (
    COST_GUARD,
    HARNESS,
    ChangedTask,
    GateDecision,
    GateGuards,
    GateRule,
    GuardResult,
    RunSet,
    Tokens,
    TrialResult,
    compare,
    rule_hash,
)
from lamplighter.ports import Failure

RULE = GateRule(
    metric="pass_fraction_per_task",
    method="paired_sign_flip_mid_p",
    alpha_permille=50,
    trials_per_task=3,
    infra_limit_permille=50,
    guards=GateGuards(cost_per_valid_trial_permille=1250),
)
# Letters for a trial's outcome: a pass, a fail, an infra trial, and an infra
# trial stopped by the run set's money.
_OUTCOMES = {
    "p": ("pass", None),
    "f": ("fail", None),
    "i": ("infra", "harness_failure"),
    "b": ("infra", "budget_reached"),
}


def _trial(task: str, attempt: int, letter: str, cost: int | None) -> TrialResult:
    outcome, reason = _OUTCOMES[letter]
    return TrialResult(
        task=task,
        attempt=attempt,
        outcome=outcome,  # pyright: ignore[reportArgumentType]
        reason=reason,  # pyright: ignore[reportArgumentType]
        run_reason="done",
        steps=3,
        tool_calls=2,
        tokens=Tokens(1, 2, 3, 4),
        micro_dollars=cost,
        ms=1_000,
        runner_name=f"{task}__{attempt}",
        task_folder_hash="h" * 64,
    )


def _side(name: str, tasks: dict[str, str], cost: int | None = 1_000) -> RunSet:
    """A run set of the harness: each task's letters are its attempts in order."""
    return RunSet(
        run_set=name,
        harness=HARNESS,
        harness_version="1.0",
        portable_config_id=name * 4,
        code_revision="unknown",
        task_set_hash="t" * 64,
        split="dev",
        trials_per_task=len(next(iter(tasks.values()), "ppp")),
        model="made-up-1",
        provider="made-up",
        trials=tuple(
            _trial(task, attempt, letter, cost)
            for task, letters in tasks.items()
            for attempt, letter in enumerate(letters, start=1)
        ),
    )


def _tasks(count: int, letters: str) -> dict[str, str]:
    return {f"task-{n:02d}": letters for n in range(count)}


def _with(tasks: dict[str, str], **changed: str) -> dict[str, str]:
    return {**tasks, **{name.replace("_", "-"): v for name, v in changed.items()}}


def _decided(
    baseline: RunSet, candidate: RunSet, rule: GateRule = RULE
) -> GateDecision:
    decision = compare(baseline, candidate, rule)
    assert isinstance(decision, GateDecision), decision
    return decision


SAME = _tasks(20, "ppf")
FOUR_UP = _with(
    _tasks(20, "pff"), task_00="ppf", task_01="ppf", task_02="ppf", task_03="ppf"
)


def test_four_tasks_up_one_attempt_in_twenty_is_kept() -> None:
    decision = _decided(_side("a", _tasks(20, "pff")), _side("b", FOUR_UP))
    assert (decision.outcome, decision.why) == ("keep", "better")
    assert decision.rule_hash == rule_hash(RULE)
    assert (decision.p_better_ppm, decision.p_worse_ppm) == (31_250, 968_750)
    assert (decision.tasks, decision.tasks_up) == (20, 4)
    assert (decision.tasks_down, decision.tasks_same) == (0, 16)
    assert decision.difference_bp == 667
    assert (decision.interval_low_bp, decision.interval_high_bp) == (139, 1195)
    assert decision.changed == tuple(
        ChangedTask(f"task-0{n}", 1, 3, 2, 3) for n in range(4)
    )
    assert decision.baseline.mean_pass_bp == 3333
    assert decision.candidate.mean_pass_bp == 4000


def test_three_tasks_up_one_attempt_in_twenty_is_inconclusive() -> None:
    three = _with(_tasks(20, "pff"), task_00="ppf", task_01="ppf", task_02="ppf")
    decision = _decided(_side("a", _tasks(20, "pff")), _side("b", three))
    assert (decision.outcome, decision.why) == ("inconclusive", "no_clear_difference")
    assert decision.p_better_ppm == 62_500


def test_four_tasks_down_one_attempt_in_twenty_is_rejected() -> None:
    decision = _decided(_side("a", FOUR_UP), _side("b", _tasks(20, "pff")))
    assert (decision.outcome, decision.why) == ("reject", "worse")
    assert decision.p_worse_ppm == 31_250


def test_the_p_values_are_rounded_up_in_millionths() -> None:
    # Six tasks up one attempt and two down: 23 / 256 and 233 / 256.
    baseline = _tasks(20, "pff")
    candidate = dict(baseline)
    for n in range(6):
        candidate[f"task-{n:02d}"] = "ppf"
    candidate["task-06"] = candidate["task-07"] = "fff"
    decision = _decided(_side("a", baseline), _side("b", candidate))
    assert (decision.p_better_ppm, decision.p_worse_ppm) == (89_844, 910_157)
    assert decision.outcome == "inconclusive"


def test_a_keep_at_a_third_of_the_floor_rests_on_infra_trials() -> None:
    # Three tasks go from 1 pass in 3 to 1 in 2 by a lost trial, and one from
    # 1 in 2 to 2 in 3: four tasks up a sixth each, which is 3.33 points.
    baseline = _with(_tasks(20, "ppf"), **{f"task_0{n}": "pff" for n in range(3)})
    baseline["task-03"] = "pfi"
    candidate = _with(_tasks(20, "ppf"), **{f"task_0{n}": "pfi" for n in range(3)})
    decision = _decided(_side("a", baseline), _side("b", candidate))
    assert (decision.outcome, decision.difference_bp) == ("keep", 333)
    assert decision.changed[0] == ChangedTask("task-00", 1, 3, 1, 2)
    assert decision.changed[3] == ChangedTask("task-03", 1, 2, 2, 3)


def test_the_counts_of_each_side() -> None:
    candidate = _with(FOUR_UP, task_04="ppp", task_05="ppi")
    decision = _decided(_side("a", _tasks(20, "pff")), _side("b", candidate))
    summary = decision.candidate
    assert (summary.passes, summary.fails, summary.infra) == (27, 32, 1)
    assert (summary.planned, summary.tasks_all_passed) == (60, 1)
    assert (summary.tasks_any_passed, summary.micro_dollars_per_valid_trial) == (
        20,
        1_000,
    )


# The eight laws.


def _random_side(draw: random.Random, name: str, chances: dict[str, float]) -> RunSet:
    def letter(chance: float) -> str:
        if draw.random() < 0.005:
            return "i"
        return "p" if draw.random() < chance else "f"

    side = _side(
        name, {t: "".join(letter(c) for _ in range(3)) for t, c in chances.items()}
    )
    costs = (900, 1_000, 1_100, 1_300)
    trials = tuple(
        dataclasses.replace(
            t, micro_dollars=None if draw.random() < 0.002 else draw.choice(costs)
        )
        for t in side.trials
    )
    return dataclasses.replace(side, trials=trials)


def _random_pairs(count: int) -> list[tuple[RunSet, RunSet]]:
    # Each pair has a drift, so that keeps, rejects and neither all occur.
    draw = random.Random(20261128)
    pairs: list[tuple[RunSet, RunSet]] = []
    for _ in range(count):
        drift = draw.choice((-0.5, -0.25, 0.0, 0.25, 0.5))
        chances = {f"task-{n:02d}": draw.random() for n in range(draw.randint(1, 30))}
        moved = {t: min(1.0, max(0.0, c + drift)) for t, c in chances.items()}
        pairs.append((_random_side(draw, "a", chances), _random_side(draw, "b", moved)))
    return pairs


PAIRS = _random_pairs(600)


def test_law_one_the_same_inputs_give_the_same_bytes_in_two_processes() -> None:
    program = (
        "from lamplighter.ports import canonical_json\n"
        "from tests.bench.test_gate import PAIRS, RULE, compare\n"
        "print(''.join(canonical_json(compare(a, b, RULE)) for a, b in PAIRS[:60]))\n"
    )
    outputs = [
        subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2")
    ]
    assert outputs[0] == outputs[1]
    assert outputs[0].count('"rule_hash"') == 60


def test_law_two_swapping_the_sides_turns_a_keep_into_a_reject() -> None:
    kept = 0
    for baseline, candidate in PAIRS:
        one = _decided(baseline, candidate)
        other = _decided(candidate, baseline)
        if one.outcome == "keep":
            kept += 1
            assert other.outcome == "reject"
        if one.outcome == "inconclusive":
            assert other.outcome == "inconclusive"
    assert kept > 0


def test_law_three_a_run_set_compared_with_itself_is_inconclusive() -> None:
    for baseline, _ in PAIRS:
        assert _decided(baseline, baseline).outcome == "inconclusive"


@pytest.mark.parametrize("field", ["task_set_hash", "code_revision"])
def test_law_four_another_task_set_or_code_cannot_be_compared(field: str) -> None:
    candidate = dataclasses.replace(_side("b", SAME), **{field: "other"})
    refused = compare(_side("a", SAME), candidate, RULE)
    assert isinstance(refused, Failure)
    assert refused.kind == "invalid_input"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_law_five_infra_above_the_limit_is_inconclusive(side: str) -> None:
    lossy = _with(SAME, task_00="ppi", task_01="ppi", task_02="ppi", task_03="ppi")
    sides = {"baseline": _side("a", FOUR_UP), "candidate": _side("b", FOUR_UP)}
    sides[side] = _side(side, lossy)
    decision = _decided(sides["baseline"], sides["candidate"])
    assert (decision.outcome, decision.why) == ("inconclusive", "infra_over_limit")


def test_law_six_fourteen_tasks_lost_in_twenty_are_rejected_whatever_the_rest() -> None:
    draw = random.Random(6)
    for _ in range(300):
        rest = [f"task-{n:02d}" for n in range(14, 20)]
        baseline = _tasks(14, "ppp")
        candidate = _tasks(14, "fff")
        for task in rest:
            baseline[task] = "".join(draw.choice("pf") for _ in range(3))
            candidate[task] = "".join(draw.choice("pf") for _ in range(3))
        decision = _decided(_side("a", baseline), _side("b", candidate))
        assert (decision.outcome, decision.why) == ("reject", "worse")


def test_law_six_holds_at_its_size_only() -> None:
    baseline = {**_tasks(14, "ppp")}
    candidate = {**_tasks(14, "fff")}
    for n in range(14, 21):
        baseline[f"task-{n:02d}"] = "fff"
        candidate[f"task-{n:02d}"] = "ppp"
    decision = _decided(_side("a", baseline), _side("b", candidate))
    assert decision.outcome == "inconclusive"


def test_law_seven_a_guard_only_turns_a_keep_into_a_reject() -> None:
    tight = dataclasses.replace(RULE, guards=GateGuards(1))
    loose = dataclasses.replace(RULE, guards=GateGuards(10**9))
    turned = 0
    for baseline, candidate in PAIRS:
        free = _decided(baseline, candidate, loose)
        held = _decided(baseline, candidate, tight)
        if free.outcome == "keep" and held.outcome != "keep":
            turned += 1
            assert (held.outcome, held.why) == ("reject", "guard_failed")
        else:
            assert (held.outcome, held.why) == (free.outcome, free.why)
    assert turned > 0


def test_law_eight_the_order_of_tasks_and_trials_changes_nothing() -> None:
    draw = random.Random(8)
    for baseline, candidate in PAIRS[:200]:
        shuffled = [
            dataclasses.replace(s, trials=tuple(draw.sample(s.trials, len(s.trials))))
            for s in (baseline, candidate)
        ]
        assert compare(shuffled[0], shuffled[1], RULE) == compare(
            baseline, candidate, RULE
        )


# The steps before the test.


def test_one_trial_stopped_by_the_money_makes_it_inconclusive() -> None:
    decision = _decided(_side("a", SAME), _side("b", _with(FOUR_UP, task_19="ppb")))
    assert (decision.outcome, decision.why) == ("inconclusive", "budget_reached")
    assert decision.tasks == 20
    assert decision.candidate.infra == 1
    assert decision.candidate.mean_pass_bp is None
    assert decision.candidate.micro_dollars_per_valid_trial == 1_000
    for value in (
        decision.tasks_up,
        decision.tasks_down,
        decision.tasks_same,
        decision.difference_bp,
        decision.interval_low_bp,
        decision.interval_high_bp,
        decision.p_better_ppm,
        decision.p_worse_ppm,
        decision.baseline.mean_pass_bp,
    ):
        assert value is None
    assert (decision.guards, decision.changed) == ((), ())


def test_the_infra_limit_is_reached_at_three_in_sixty_and_passed_at_four() -> None:
    three = _with(FOUR_UP, task_10="ppi", task_11="ppi", task_12="ppi")
    at_limit = _decided(_side("a", _tasks(20, "pff")), _side("b", three))
    assert at_limit.why != "infra_over_limit"
    four = _with(three, task_13="ppi")
    over = _decided(_side("a", _tasks(20, "pff")), _side("b", four))
    assert over.why == "infra_over_limit"


def test_a_task_with_no_valid_trial_makes_it_inconclusive() -> None:
    decision = _decided(_side("a", SAME), _side("b", _with(SAME, task_05="iii")))
    assert (decision.outcome, decision.why) == (
        "inconclusive",
        "task_without_valid_trial",
    )


# The guard.


def test_a_dearer_better_candidate_is_rejected_by_the_guard() -> None:
    decision = _decided(_side("a", _tasks(20, "pff")), _side("b", FOUR_UP, 2_000))
    assert (decision.outcome, decision.why) == ("reject", "guard_failed")
    assert decision.guards == (GuardResult(COST_GUARD, False, 1_000, 2_000),)


def test_a_guard_that_fails_on_a_reject_or_an_inconclusive_changes_nothing() -> None:
    worse = _decided(_side("a", FOUR_UP), _side("b", _tasks(20, "pff"), 2_000))
    assert (worse.outcome, worse.why) == ("reject", "worse")
    assert not worse.guards[0].passed
    same = _decided(_side("a", SAME), _side("b", SAME, 2_000))
    assert (same.outcome, same.why) == ("inconclusive", "no_clear_difference")


def test_the_guard_holds_at_exactly_its_share_and_fails_a_micro_dollar_over() -> None:
    baseline = _side("a", _tasks(20, "pff"))
    at = _decided(baseline, _side("b", FOUR_UP, 1_250))
    assert (at.outcome, at.guards[0].passed) == ("keep", True)
    over = _side("b", FOUR_UP, 1_250)
    first = dataclasses.replace(over.trials[0], micro_dollars=1_251)
    over = dataclasses.replace(over, trials=(first, *over.trials[1:]))
    assert _decided(baseline, over).why == "guard_failed"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_a_valid_trial_of_unknown_cost_fails_the_guard(side: str) -> None:
    sides = {
        "baseline": _side("a", _tasks(20, "pff")),
        "candidate": _side("b", FOUR_UP),
    }
    unknown = sides[side]
    first = dataclasses.replace(unknown.trials[0], micro_dollars=None)
    sides[side] = dataclasses.replace(unknown, trials=(first, *unknown.trials[1:]))
    decision = _decided(sides["baseline"], sides["candidate"])
    assert decision.why == "guard_failed"
    assert getattr(decision.guards[0], f"{side}_micro_dollars_per_valid_trial") is None


def test_an_infra_trial_of_unknown_cost_is_not_counted() -> None:
    candidate = _side("b", _with(FOUR_UP, task_19="ppi"))
    trials = tuple(
        dataclasses.replace(t, micro_dollars=None) if t.outcome == "infra" else t
        for t in candidate.trials
    )
    decision = _decided(
        _side("a", _tasks(20, "pff")), dataclasses.replace(candidate, trials=trials)
    )
    assert (decision.outcome, decision.guards[0].passed) == ("keep", True)


def test_the_cost_for_each_valid_trial_is_rounded_down() -> None:
    candidate = _side("b", FOUR_UP)
    first = dataclasses.replace(candidate.trials[0], micro_dollars=1_059)
    candidate = dataclasses.replace(candidate, trials=(first, *candidate.trials[1:]))
    decision = _decided(_side("a", _tasks(20, "pff")), candidate)
    # 59 more over 60 trials is just under one more for each.
    assert decision.candidate.micro_dollars_per_valid_trial == 1_000


# What cannot be compared.


def _refused(baseline: RunSet, candidate: RunSet, rule: GateRule = RULE) -> None:
    refused = compare(baseline, candidate, rule)
    assert isinstance(refused, Failure), refused
    assert (refused.kind, refused.effect) == ("invalid_input", "none")


@pytest.mark.parametrize(
    "change",
    [
        {"task_set_hash": "other"},
        {"code_revision": "other"},
        {"split": "held_out"},
        {"harness": "made-up-baseline"},
    ],
)
def test_two_run_sets_that_differ_in_what_was_run_are_refused(
    change: dict[str, str],
) -> None:
    _refused(_side("a", SAME), dataclasses.replace(_side("b", SAME), **change))
    _refused(dataclasses.replace(_side("a", SAME), **change), _side("b", SAME))


def test_another_number_of_trials_per_task_is_refused() -> None:
    _refused(_side("a", _tasks(20, "pf")), _side("b", SAME))
    _refused(_side("a", _tasks(20, "pf")), _side("b", _tasks(20, "pf")))


def test_two_sets_of_task_names_are_refused() -> None:
    other = {**SAME}
    other["task-99"] = other.pop("task-00")
    _refused(_side("a", SAME), _side("b", other))


@pytest.mark.parametrize("count", [0, 31])
def test_no_task_or_more_than_thirty_is_refused(count: int) -> None:
    tasks = _tasks(count, "ppf")
    _refused(_side("a", tasks), _side("b", tasks))


def test_thirty_tasks_are_compared() -> None:
    assert _decided(_side("a", _tasks(30, "ppf")), _side("b", _tasks(30, "ppf")))


@pytest.mark.parametrize("attempts", [(1, 2, 2), (1, 2, 4), (0, 1, 2), (1, 2)])
def test_attempts_that_are_not_one_to_three_each_once_are_refused(
    attempts: tuple[int, ...],
) -> None:
    side = _side("b", SAME)
    rest = tuple(t for t in side.trials if t.task != "task-00")
    first = side.trials[0]
    broken = tuple(dataclasses.replace(first, attempt=a) for a in attempts)
    _refused(_side("a", SAME), dataclasses.replace(side, trials=broken + rest))


def test_an_outcome_that_is_not_one_of_the_three_words_is_refused() -> None:
    side = _side("b", SAME)
    # A run set read back from the ledger could hold any word.
    odd = dataclasses.replace(side.trials[0], outcome="maybe")  # pyright: ignore[reportArgumentType]
    _refused(
        _side("a", SAME), dataclasses.replace(side, trials=(odd, *side.trials[1:]))
    )


def test_two_run_sets_of_a_baseline_harness_are_refused() -> None:
    baseline = dataclasses.replace(_side("a", SAME), harness="made-up-baseline")
    candidate = dataclasses.replace(_side("b", FOUR_UP), harness="made-up-baseline")
    _refused(baseline, candidate)
