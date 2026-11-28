"""The bench's settings, the rule and the pinned Harbor version, each from its file."""

from pathlib import Path

from lamplighter.app.bench_settings import (
    BenchSettings,
    harbor_version,
    load_bench_settings,
    read_rule,
)
from lamplighter.app.code_revision import REPO_ROOT
from lamplighter.bench import GateRule
from lamplighter.ports import Failure
from tests.bench_kit import RULE

SETTINGS = """\
dir = "~/made-up-bench"
shell_path = "/usr/bin:/bin"
run_set_max_micro_dollars = 30_000_000
concurrent_trials = 4
"""


def _written(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bench.toml"
    path.write_text(text)
    return path


def test_the_settings_are_read_with_the_home_folder_filled_in(tmp_path: Path) -> None:
    found = load_bench_settings(_written(tmp_path, SETTINGS), Path("/made/up/home"))
    assert found == BenchSettings(
        "/made/up/home/made-up-bench", "/usr/bin:/bin", 30_000_000, 4
    )


def test_settings_that_do_not_fit_are_invalid_input(tmp_path: Path) -> None:
    home = Path("/made/up/home")
    for text in (
        SETTINGS + "spare = 1\n",
        SETTINGS.replace("concurrent_trials = 4", "concurrent_trials = 0"),
        SETTINGS.replace('"~/made-up-bench"', '"made-up-bench"'),
        "not toml = = 1",
    ):
        found = load_bench_settings(_written(tmp_path, text), home)
        assert isinstance(found, Failure) and found.kind == "invalid_input", text
    missing = load_bench_settings(tmp_path / "missing.toml", home)
    assert isinstance(missing, Failure) and "could not be read" in missing.message


def test_the_rule_file_is_read_with_the_rules_checks(tmp_path: Path) -> None:
    path = tmp_path / "gate_rule.toml"
    path.write_text(RULE)
    assert isinstance(read_rule(path), GateRule)
    path.write_text(RULE.replace("trials_per_task = 3", "trials_per_task = 4"))
    found = read_rule(path)
    assert isinstance(found, Failure) and "trials_per_task" in found.message


def test_the_harbor_version_is_the_one_the_bench_lock_pins(tmp_path: Path) -> None:
    assert harbor_version(REPO_ROOT / "bench" / "harbor" / "uv.lock") == "0.27.0"
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "made-up"\nversion = "1.0"\n')
    found = harbor_version(lock)
    assert isinstance(found, Failure) and "one version of harbor" in found.message


def test_the_checked_in_bench_settings_are_the_specs(tmp_path: Path) -> None:
    found = load_bench_settings(REPO_ROOT / "bench" / "bench.toml", tmp_path)
    assert found == BenchSettings(
        str(tmp_path / "lamplighter-bench"),
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        30_000_000,
        4,
    )
