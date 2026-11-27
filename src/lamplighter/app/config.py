"""Reads the settings file."""

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.builtin_tools import ToolSettings
from lamplighter.core import Prices, RunSettings
from lamplighter.ports import ModelProfile, from_plain

# Folders a sync tool may copy while SQLite has the file open, which can
# damage it. The first two are the folders iCloud's "Desktop and Documents"
# option syncs. The last two hold iCloud Drive and other sync tools' folders.
_SYNCED_IN_HOME = ("Documents", "Desktop")
_SYNCED_ANYWHERE = ("/Library/Mobile Documents/", "/Library/CloudStorage/")
# The largest text a tool can give is 8,388,608 bytes read in, which as
# outside text and saved as JSON stays under this. It must fit in one blob.
_LEAST_BLOB_BYTES = 26_214_400
# A saved result must fit in one record. The worst text is outside text of
# control characters: each is seven bytes once wrapped and saved as JSON,
# and a character outside ASCII is at most four.
_RECORD_BYTES_PER_CAP_CHAR = 8
_RECORD_ROOM = 65_536
_RANGES = {
    "shell_max_seconds": (1, 3_600),
    "output_cap_chars": (26_000, 200_000),
    "fetch_max_bytes": (1, 8_388_608),
    "fetch_seconds": (1, 600),
}


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
    tools: ToolSettings


def load_config(path: Path) -> Config:
    """Read the settings file at `path`.

    Raises ValueError when the file has a missing, extra or wrongly typed
    setting, a run or tool setting out of its range, or a folder that is
    not allowed. A wrong settings file is a mistake to fix before anything
    runs.
    """
    with path.open("rb") as file:
        config = from_plain(Config, tomllib.load(file))
    check_run(config.run, config.model)
    home = Path.home()
    store = dataclasses.replace(
        config.store, dir=str(store_folder(config.store.dir, home))
    )
    tools = tool_settings(config.tools, home)
    check_across(store, tools)
    return dataclasses.replace(config, store=store, tools=tools)


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


def tool_settings(tools: ToolSettings, home: Path) -> ToolSettings:
    """Give the tool settings with `~` filled in, or raise ValueError.

    The folders are not resolved: the chat's start refuses one that is
    reached through a link, so the settings must name the real place.
    """
    for name, (least, most) in _RANGES.items():
        value = getattr(tools, name)
        if not least <= value <= most:
            raise ValueError(f"tools.{name} must be from {least} to {most}: {value}")
    if not 1 <= tools.shell_default_seconds <= tools.shell_max_seconds:
        raise ValueError(
            "tools.shell_default_seconds must be from 1 to tools.shell_max_seconds,"
            f" not {tools.shell_default_seconds}"
        )
    parts = tools.shell_path.split(":")
    if not tools.shell_path or not all(Path(part).is_absolute() for part in parts):
        raise ValueError("every part of tools.shell_path must be an absolute path")
    return dataclasses.replace(
        tools,
        work_dir=str(_tool_folder("tools.work_dir", tools.work_dir, home)),
        scratch_dir=str(_tool_folder("tools.scratch_dir", tools.scratch_dir, home)),
    )


def check_across(store: StoreSettings, tools: ToolSettings) -> None:
    """Raise ValueError for settings of two sections that do not fit together."""
    folders = {
        "store.dir": Path(store.dir).resolve(),
        "tools.work_dir": Path(tools.work_dir).resolve(),
        "tools.scratch_dir": Path(tools.scratch_dir).resolve(),
    }
    for name, folder in folders.items():
        for other, place in folders.items():
            if name != other and folder.is_relative_to(place):
                raise ValueError(f"{name} is {other} or inside it")
    if store.max_blob_bytes < _LEAST_BLOB_BYTES:
        raise ValueError(
            f"store.max_blob_bytes must be at least {_LEAST_BLOB_BYTES}, so the"
            " largest tool result fits in a blob"
        )
    least = _RECORD_BYTES_PER_CAP_CHAR * tools.output_cap_chars + _RECORD_ROOM
    if store.max_record_bytes < least:
        raise ValueError(
            f"store.max_record_bytes must be at least {least}: 8 times"
            " tools.output_cap_chars, and 65,536"
        )


def _tool_folder(name: str, setting: str, home: Path) -> Path:
    if setting == "~" or setting.startswith("~/"):
        folder = home / setting[2:]
    elif Path(setting).is_absolute():
        folder = Path(setting)
    else:
        raise ValueError(f"{name} must start with ~/ or /, not {setting!r}")
    # A command may change anything in a tool folder, so one that holds the
    # home folder would put all of it in reach.
    if home.is_relative_to(folder):
        raise ValueError(f"{name} must not be {folder}, which holds the home folder")
    return folder
