"""The bench's checked-in task set, split, rule and planted worse config."""

import dataclasses
from collections import Counter

from lamplighter.app.bench_parts import task_view
from lamplighter.app.bench_settings import read_rule
from lamplighter.app.code_revision import REPO_ROOT
from lamplighter.app.config import load_config
from lamplighter.app.main import settings_portable_id
from lamplighter.app.task_set_view import TaskSetView
from lamplighter.bench import GROUPS, GateRule, rule_hash

BENCH = REPO_ROOT / "bench"
# The dev tasks the spec names, worked out from its seed.
DEV = (
    "add-a-flag",
    "edit-second-match",
    "fetch-is-off",
    "line-in-large-file",
    "link-in-the-way",
    "monthly-totals",
    "needle-in-long-output",
    "off-by-one",
    "sort-into-folders",
    "wrong-file-name",
)


def test_the_task_set_has_no_fault_and_six_groups_of_five() -> None:
    view = task_view(REPO_ROOT)
    assert isinstance(view, TaskSetView)
    assert view.faults == ()
    assert len(view.groups) == 30
    assert Counter(view.groups.values()) == {group: 5 for group in GROUPS}


def test_the_split_is_the_one_the_spec_names() -> None:
    view = task_view(REPO_ROOT)
    assert isinstance(view, TaskSetView) and view.split is not None
    assert view.split.dev == DEV
    assert len(view.split.held_out) == 20


def test_the_rule_is_the_one_the_spec_names() -> None:
    rule = read_rule(BENCH / "gate_rule.toml")
    assert isinstance(rule, GateRule)
    assert rule_hash(rule) == (
        "914fe7ad09f9cc3cf30f2a815cbc4aa826bc2f658c2240a5bf3880ec72574f32"
    )


def test_the_planted_worse_config_differs_only_in_max_steps() -> None:
    baseline = load_config(REPO_ROOT / "lamplighter.toml")
    worse = load_config(BENCH / "configs" / "planted_worse.toml")
    assert worse.run.max_steps == 1
    run = dataclasses.replace(worse.run, max_steps=baseline.run.max_steps)
    assert dataclasses.replace(worse, run=run) == baseline
    assert settings_portable_id(worse) != settings_portable_id(baseline)


def test_the_local_configs_differ_only_in_their_models_and_route() -> None:
    baseline = load_config(REPO_ROOT / "lamplighter.toml")
    only = load_config(BENCH / "configs" / "local_only.toml")
    first = load_config(BENCH / "configs" / "local_first.toml")
    (local,) = only.models
    assert local.name == "local" and local.local is not None
    assert local.profile.stays_on_mac
    assert first.models == (baseline.default_model(), local)
    assert [(r.id, r.when, r.models) for r in only.routes] == [
        ("default", "always", ("local",))
    ]
    assert [(r.id, r.when, r.models) for r in first.routes] == [
        ("default", "always", ("local", "frontier"))
    ]
    for config in (only, first):
        same = dataclasses.replace(
            config, models=baseline.models, routes=baseline.routes
        )
        assert same == baseline
