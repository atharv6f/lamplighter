"""Reads the settings file."""

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path

from lamplighter.core import Prices
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


def load_config(path: Path) -> Config:
    """Read the settings file at `path`.

    Raises ValueError when the file has a missing, extra or wrongly typed
    setting, or a store folder that is not allowed. A wrong settings file is
    a mistake to fix before anything runs.
    """
    with path.open("rb") as file:
        config = from_plain(Config, tomllib.load(file))
    folder = store_folder(config.store.dir, Path.home())
    return dataclasses.replace(
        config, store=dataclasses.replace(config.store, dir=str(folder))
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
