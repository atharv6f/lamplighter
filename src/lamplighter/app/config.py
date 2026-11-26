"""Reads the settings file."""

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path

from lamplighter.core import Prices, RunSettings
from lamplighter.ports import ModelProfile, from_plain

# Folders a sync tool may copy while SQLite has the file open, which can
# damage it. The first two are the folders iCloud's "Desktop and Documents"
# option syncs. The last two hold iCloud Drive and other sync tools' folders.
_SYNCED_IN_HOME = ("Documents", "Desktop")
_SYNCED_ANYWHERE = ("/Library/Mobile Documents/", "/Library/CloudStorage/")


@dataclass(frozen=True, slots=True)
class StoreSettings:
    """Where the saved state lives and how large one saved value may be.

    `dir` is an absolute path once the file is read. It must not be in a
    folder that a sync tool copies. No check can find every such folder, so
    this rule also stands beside the setting in the settings file.
    """

    dir: str
    max_record_bytes: int
    max_blob_bytes: int


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the settings file holds."""

    model: ModelProfile
    prices: Prices
    store: StoreSettings
    run: RunSettings


def load_config(path: Path) -> Config:
    """Read the settings file at `path`.

    Raises ValueError when the file has a missing, extra or wrongly typed
    setting, a run setting out of its range, or a store folder that is not
    allowed. A wrong settings file is a mistake to fix before anything runs.
    """
    with path.open("rb") as file:
        config = from_plain(Config, tomllib.load(file))
    check_run(config.run, config.model)
    folder = store_folder(config.store.dir, Path.home())
    return dataclasses.replace(
        config, store=dataclasses.replace(config.store, dir=str(folder))
    )


def check_run(run: RunSettings, model: ModelProfile) -> None:
    """Raise ValueError for a run setting no run could keep to."""
    if not run.system.strip():
        raise ValueError("run.system is empty")
    limits = {
        "run.max_steps": run.max_steps,
        "run.max_seconds": run.max_seconds,
        "run.max_tokens": run.max_tokens,
        "run.max_micro_dollars": run.max_micro_dollars,
        "run.model_max_output": run.model_max_output,
        "run.model_deadline_ms": run.model_deadline_ms,
        "run.cancel_bound_ms": run.cancel_bound_ms,
    }
    for name, value in limits.items():
        if value < 1:
            raise ValueError(f"{name} must be at least 1, not {value}")
    if run.model_retries < 0:
        raise ValueError(f"run.model_retries must not be negative: {run.model_retries}")
    if run.model_max_output > model.max_output:
        raise ValueError(
            f"run.model_max_output is {run.model_max_output}, above the"
            f" model's max_output of {model.max_output}"
        )


def store_folder(setting: str, home: Path) -> Path:
    """Give the absolute store folder a setting names, or raise ValueError.

    A setting starts with ~/ for the home folder, or is an absolute path.
    """
    if setting == "~" or setting.startswith("~/"):
        folder = home / setting[2:]
    elif Path(setting).is_absolute():
        folder = Path(setting)
    else:
        raise ValueError(f"store.dir must start with ~/ or /, not {setting!r}")
    real = folder.resolve()
    for name in _SYNCED_IN_HOME:
        if real.is_relative_to((home / name).resolve()):
            raise ValueError(f"store.dir is inside ~/{name}, which iCloud may sync")
    if any(part in f"{real}/" for part in _SYNCED_ANYWHERE):
        raise ValueError("store.dir is inside a folder that a sync tool copies")
    return real
