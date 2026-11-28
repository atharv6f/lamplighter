"""The bake-off's rule, read with its checks, and the choice it makes from made-up rows.

The pinned hash was worked out once with sha256sum in a shell, from the
canonical JSON of the repo's rule file, so a change to the rule or to how
it is hashed shows up here.
"""

import dataclasses
import tomllib
from pathlib import Path
from typing import Any

import pytest

from lamplighter.bench import (
    BakeoffChoice,
    BakeoffRule,
    Candidate,
    CandidateMeasures,
    FirstToken,
    PeakMemory,
    SoakCounts,
    SoakMeasures,
    Speeds,
    UsableReplies,
    bakeoff_choice,
    bakeoff_rule_hash,
    read_bakeoff_rule,
    stays_up,
)
from lamplighter.ports import (
    Failure,
    JsonObject,
    JsonValue,
    LedgerRow,
    Verdict,
    to_plain,
)
from tests.ledger_kit import (
    bakeoff_candidate,
    bakeoff_rule,
    bakeoff_soak,
)

RULE_FILE = Path(__file__).parents[2] / "bench" / "bakeoff_rule.toml"
RULE_HASH = "e87050e27fcab5f1556f39d7ee5986f9d4fd8a2573e75d929e82ac741fe0c833"
RULE = BakeoffRule(
    memory_limit_bytes=32_000_000_000,
    tie_points=3,
    pressure_limit=4,
    capture_window=65_536,
    capture_max_tokens=16_000,
)
GB = 1_000_000_000
ALL_PASS = tuple(
    Verdict(name, True, "Made up.")
    for name in (
        "broken_call_visible",
        "prompt_over_window_caught",
        "loopback_only",
        "pinned",
    )
)


def _rule_plain() -> JsonValue:
    return tomllib.loads(RULE_FILE.read_text())


def test_the_repo_s_rule_is_the_design_s_and_has_its_hash() -> None:
    rule = read_bakeoff_rule(_rule_plain())
    assert rule == RULE
    assert bakeoff_rule_hash(RULE) == RULE_HASH


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"extra": 1}, "expected form"),
        ({"tie_points": 101}, "tie_points"),
        ({"tie_points": -1}, "tie_points"),
        ({"memory_limit_bytes": 0}, "limits and window"),
        ({"pressure_limit": 0}, "limits and window"),
        ({"capture_max_tokens": 65_536}, "capture_max_tokens"),
        ({"capture_max_tokens": 0}, "capture_max_tokens"),
    ],
)
def test_a_rule_out_of_range_is_refused(change: dict[str, int], said: str) -> None:
    plain: dict[str, JsonValue] = {**dataclasses.asdict(RULE), **change}
    answer = read_bakeoff_rule(plain)
    assert isinstance(answer, Failure)
    assert said in answer.message


# The soak.


def _counts(**changed: Any) -> SoakCounts:
    counts = SoakCounts(
        whole=True,
        requests=1440,
        failed=0,
        first_hour_peak_bytes=20 * GB,
        end_bytes=22 * GB,
        first_hour_median_ms=2_000,
        last_hour_median_ms=3_000,
    )
    return dataclasses.replace(counts, **changed)


def test_a_soak_at_each_limit_passes() -> None:
    assert stays_up(_counts())


@pytest.mark.parametrize(
    "changed",
    [
        {"whole": False},
        {"requests": 0},
        {"failed": 1},
        {"end_bytes": 22 * GB + 1},
        {"last_hour_median_ms": 3_001},
    ],
)
def test_a_soak_past_any_limit_fails(changed: dict[str, Any]) -> None:
    assert not stays_up(_counts(**changed))


# The choice.


def _measures(
    usable: int = 190,
    speed: int = 30_000,
    footprint: int = 20 * GB,
    added: int = 0,
    pressure: int = 1,
) -> CandidateMeasures:
    return CandidateMeasures(
        usable_replies=UsableReplies(
            usable=usable,
            total=200,
            each=("usable",) * usable + ("unparsed",) * (200 - usable),
            calls=150,
            good_calls=148,
        ),
        first_token_ms=FirstToken(median_ms=400, each_ms=(400,) * 5),
        tokens_per_second=Speeds(
            prompt_8k_milli=900_000,
            generation_8k_milli=speed + 5_000,
            prompt_32k_milli=700_000,
            generation_32k_milli=speed,
            server_timings={},
        ),
        peak_memory=PeakMemory(
            footprint_bytes=footprint,
            added_bytes=added,
            pressure_level=pressure,
            reads=300,
            longest_gap_ms=1_400,
        ),
        adapter_per_request="no",
        adapter_evidence="Made up.",
        forced_schema=Verdict("forced_schema", True, "Made up."),
    )


def _plain_object(record: object) -> JsonObject:
    plain = to_plain(record)
    assert isinstance(plain, dict)
    return plain


def _rule_row(rule: BakeoffRule = RULE) -> LedgerRow:
    row = bakeoff_rule(rule_hash=bakeoff_rule_hash(rule))
    body = dataclasses.replace(row.body, rule=_plain_object(rule))
    return dataclasses.replace(row, body=body)


def _measured(
    server: str,
    model: str = "made-up-27b",
    measures: CandidateMeasures | None = None,
    hard: tuple[Verdict, ...] = ALL_PASS,
    rule: BakeoffRule = RULE,
) -> LedgerRow:
    row = bakeoff_candidate(server, model, bakeoff_rule_hash(rule))
    criteria = _plain_object(measures or _measures())
    body = dataclasses.replace(row.body, hard=hard, criteria=criteria)
    return dataclasses.replace(row, body=body)


def _soak(server: str, passed: bool, model: str = "made-up-27b") -> LedgerRow:
    row = bakeoff_soak(passed, server, model, bakeoff_rule_hash(RULE))
    soak = SoakMeasures(passed=passed, counts=_counts())
    criteria = {"stays_up": to_plain(soak)}
    return dataclasses.replace(
        row, body=dataclasses.replace(row.body, criteria=criteria)
    )


def _chose(rows: list[LedgerRow]) -> BakeoffChoice:
    choice = bakeoff_choice(rows)
    assert not isinstance(choice, Failure), choice
    return choice


def _servers(found: tuple[Candidate, ...]) -> list[str]:
    return [c.server for c in found]


def test_no_rule_row_is_refused() -> None:
    answer = bakeoff_choice([_measured("one")])
    assert isinstance(answer, Failure)
    assert answer.message == "There is no bake-off rule row."


def test_a_failed_or_missing_hard_requirement_puts_a_candidate_out() -> None:
    failed = (*ALL_PASS[:3], Verdict("pinned", False, "Made up."))
    rows = [
        _rule_row(),
        _measured("failed", hard=failed),
        _measured("missing", hard=ALL_PASS[1:]),
        _measured("kept"),
    ]
    choice = _chose(rows)
    assert _servers(choice.order) == ["kept"]
    assert [(left.candidate.server, left.why) for left in choice.out] == [
        ("failed", "step 1: pinned did not pass"),
        ("missing", "step 1: broken_call_visible did not pass"),
    ]


def test_memory_up_to_the_limit_stays_and_above_it_is_out() -> None:
    rows = [
        _rule_row(),
        _measured("at", measures=_measures(footprint=30 * GB, added=2 * GB)),
        _measured("above", measures=_measures(footprint=30 * GB, added=2 * GB + 1)),
        _measured("warning", measures=_measures(pressure=2)),
        _measured("critical", measures=_measures(pressure=4)),
    ]
    choice = _chose(rows)
    assert sorted(_servers(choice.order)) == ["at", "warning"]
    assert [(left.candidate.server, left.why) for left in choice.out] == [
        ("above", "step 2: its peak of 32,000,000,001 bytes is above the limit"),
        ("critical", "step 2: the Mac reached pressure level 4"),
    ]


def test_measures_that_cannot_be_read_put_a_candidate_out() -> None:
    row = _measured("unread")
    body = dataclasses.replace(row.body, criteria={"usable_replies": 1})
    choice = _chose([_rule_row(), dataclasses.replace(row, body=body)])
    assert choice.order == ()
    assert choice.out[0].why.startswith("step 2: its measures cannot be read:")


def test_a_gap_of_exactly_the_tie_points_is_a_tie_ordered_by_speed() -> None:
    rows = [
        _rule_row(),
        # 95 % and 92 %: tied, so the faster goes first.
        _measured("lead", measures=_measures(usable=190, speed=20_000)),
        _measured("fast", measures=_measures(usable=184, speed=40_000)),
        # 91.5 %: just out of the tie, after both, whatever its speed.
        _measured("faster", measures=_measures(usable=183, speed=90_000)),
    ]
    assert _servers(_chose(rows).order) == ["fast", "lead", "faster"]


def test_the_others_go_by_share_after_the_tied() -> None:
    rows = [
        _rule_row(),
        _measured("lead", measures=_measures(usable=200)),
        _measured("low", measures=_measures(usable=100, speed=90_000)),
        _measured("middle", measures=_measures(usable=150, speed=10_000)),
    ]
    assert _servers(_chose(rows).order) == ["lead", "middle", "low"]


def test_equal_speeds_go_to_the_smaller_peak_then_the_names() -> None:
    rows = [
        _rule_row(),
        _measured("b-server", measures=_measures(footprint=20 * GB)),
        _measured("big", measures=_measures(footprint=24 * GB)),
        _measured("a-server", "z-model", measures=_measures(footprint=20 * GB)),
        _measured("a-server", "a-model", measures=_measures(footprint=20 * GB)),
        _measured("added", measures=_measures(footprint=19 * GB, added=2 * GB)),
    ]
    order = _chose(rows).order
    assert [(c.server, c.model) for c in order] == [
        ("a-server", "a-model"),
        ("a-server", "z-model"),
        ("b-server", "made-up-27b"),
        ("added", "made-up-27b"),
        ("big", "made-up-27b"),
    ]


def test_equal_shares_outside_the_tie_go_to_the_smaller_peak() -> None:
    rows = [
        _rule_row(),
        _measured("lead", measures=_measures(usable=200)),
        _measured("heavy", measures=_measures(usable=100, footprint=25 * GB)),
        _measured("light", measures=_measures(usable=100, footprint=21 * GB)),
    ]
    assert _servers(_chose(rows).order) == ["lead", "light", "heavy"]


def test_the_first_in_the_order_is_soaked_and_then_the_next() -> None:
    first = _measured("first", measures=_measures(speed=40_000))
    second = _measured("second", measures=_measures(speed=30_000))
    rows = [_rule_row(), first, second]
    choice = _chose(rows)
    assert (choice.to_soak, choice.chosen) == (
        Candidate("first", "made-up-27b", "w" * 64),
        None,
    )
    assert choice.why == "step 5: first made-up-27b is next to soak"
    choice = _chose([*rows, _soak("first", False)])
    assert choice.to_soak is not None and choice.to_soak.server == "second"
    choice = _chose([*rows, _soak("first", False), _soak("second", True)])
    assert choice.to_soak is None
    assert choice.chosen is not None and choice.chosen.server == "second"
    assert choice.why == (
        "steps 3 and 4: it is number 2 in the order; step 5: its soak passed,"
        " after first made-up-27b failed theirs"
    )


def test_a_passing_soak_of_the_first_chooses_it() -> None:
    rows = [_rule_row(), _measured("only"), _soak("only", True)]
    choice = _chose(rows)
    assert choice.chosen is not None and choice.chosen.server == "only"
    assert (
        choice.why
        == "steps 3 and 4: it is number 1 in the order; step 5: its soak passed"
    )


def test_none_is_chosen_when_every_soak_failed() -> None:
    rows = [_rule_row(), _measured("one"), _soak("one", False)]
    choice = _chose(rows)
    assert (choice.to_soak, choice.chosen) == (None, None)
    assert choice.why == "step 5: no candidate passed its soak"


def test_none_is_chosen_when_every_candidate_is_out() -> None:
    rows = [_rule_row(), _measured("out", measures=_measures(pressure=4))]
    choice = _chose(rows)
    assert (choice.order, choice.to_soak, choice.chosen) == ((), None, None)
    assert choice.why == "step 2: no candidate is left"


def test_a_soak_of_a_candidate_that_is_out_is_not_read() -> None:
    rows = [
        _rule_row(),
        _measured("out", measures=_measures(pressure=4)),
        _soak("out", True),
        _measured("kept"),
    ]
    choice = _chose(rows)
    assert choice.chosen is None
    assert choice.to_soak is not None and choice.to_soak.server == "kept"


def test_only_the_latest_rule_s_rows_count() -> None:
    lower = dataclasses.replace(RULE, memory_limit_bytes=21 * GB)
    rows = [
        _rule_row(),
        _measured("old"),
        _rule_row(lower),
        _measured("new", rule=lower),
        _measured("too-big", measures=_measures(footprint=22 * GB), rule=lower),
    ]
    choice = _chose(rows)
    assert choice.rule_hash == bakeoff_rule_hash(lower)
    assert _servers(choice.order) == ["new"]
    assert _servers(tuple(left.candidate for left in choice.out)) == ["too-big"]


def test_a_candidate_measured_again_keeps_its_latest_row() -> None:
    rows = [
        _rule_row(),
        _measured("again", measures=_measures(usable=100)),
        _measured("other", measures=_measures(usable=150)),
        _measured("again", measures=_measures(usable=200)),
    ]
    assert _servers(_chose(rows).order) == ["again", "other"]


def test_a_candidate_measured_on_no_request_is_out() -> None:
    empty = UsableReplies(usable=0, total=0, each=(), calls=0, good_calls=0)
    measures = dataclasses.replace(_measures(), usable_replies=empty)
    choice = _chose([_rule_row(), _measured("none", measures=measures)])
    assert [left.why for left in choice.out] == [
        "step 2: it was measured on no request"
    ]
