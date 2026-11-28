"""Loads the bench's own settings, the gate's rule and the pinned Harbor version."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from lamplighter.bench import GateRule, gate_rule
from lamplighter.ports import Failure, JsonValue, from_plain

SOURCE = "bench_settings"


@dataclass(frozen=True, slots=True)
class BenchSettings:
    """What `bench/bench.toml` holds. None of it is part of a config id.

    `dir` holds the job folders and the ledger, with `~` filled in.
    `shell_path` is the PATH a command gets in a task's container.
    """

    dir: str
    shell_path: str
    run_set_max_micro_dollars: int
    concurrent_trials: int


def load_bench_settings(path: Path, home: Path) -> BenchSettings | Failure:
    """Read the bench settings, with `~` in `dir` filled in from `home`.

    A file that cannot be read, is not TOML, lacks a setting or has one
    more, or holds a count below 1, is `invalid_input`.
    """
    plain = _toml(path)
    if isinstance(plain, Failure):
        return plain
    try:
        settings = from_plain(BenchSettings, plain)
    except ValueError as error:
        return _refused(f"{path} does not have the expected form: {error}.")
    if settings.run_set_max_micro_dollars < 1 or settings.concurrent_trials < 1:
        return _refused(f"{path}: the stop and the trials at a time must be 1 or more.")
    if settings.dir.startswith("~/"):
        return BenchSettings(
            str(home / settings.dir[2:]),
            settings.shell_path,
            settings.run_set_max_micro_dollars,
            settings.concurrent_trials,
        )
    if not settings.dir.startswith("/"):
        return _refused(f"{path}: dir must start with ~/ or /, not {settings.dir!r}.")
    return settings


def read_rule(path: Path) -> GateRule | Failure:
    """Read the gate's rule from its file, with the checks of `gate_rule`."""
    plain = _toml(path)
    return plain if isinstance(plain, Failure) else gate_rule(plain)


def harbor_version(lock: Path) -> str | Failure:
    """Give the version of Harbor that the bench's lock file pins."""
    plain = _toml(lock)
    if isinstance(plain, Failure):
        return plain
    packages = plain.get("package") if isinstance(plain, dict) else None
    found = [
        package.get("version")
        for package in (packages if isinstance(packages, list) else [])
        if isinstance(package, dict) and package.get("name") == "harbor"
    ]
    if len(found) != 1 or not isinstance(found[0], str):
        return _refused(f"{lock} does not pin one version of harbor.")
    return found[0]


def _toml(path: Path) -> JsonValue | Failure:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except OSError as error:
        return _refused(f"{path} could not be read: {error.strerror}.")
    except tomllib.TOMLDecodeError as error:
        return _refused(f"{path} is not TOML: {error}.")


def _refused(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=SOURCE)
