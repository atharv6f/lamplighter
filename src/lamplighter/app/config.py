"""Reads the settings file."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from lamplighter.core import Prices
from lamplighter.ports import ModelProfile, from_plain


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the settings file holds."""

    model: ModelProfile
    prices: Prices


def load_config(path: Path) -> Config:
    """Read the settings file at `path`.

    Raises ValueError when the file has a missing, extra or wrongly typed
    setting. A wrong settings file is a mistake to fix before anything runs.
    """
    with path.open("rb") as file:
        return from_plain(Config, tomllib.load(file))
