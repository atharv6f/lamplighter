"""Two hashes over the settings that shape a run: one for this machine, one for any."""

import hashlib
from collections.abc import Sequence

from lamplighter.core.route_table import Route
from lamplighter.core.run_spec import RunSettings
from lamplighter.ports import JsonObject, ToolEntry, saved_json


def config_id(
    models: Sequence[object],
    routes: Sequence[Route],
    run: RunSettings,
    tools: Sequence[ToolEntry],
    tool_settings: JsonObject,
    wall: JsonObject,
) -> str:
    """Give the SHA-256 of the saved JSON of the six, as 64 hex characters.

    `models` are the settings' model entries, each a record with all its
    fields and its prices, and `routes` the rule table, both in the order of
    the file. So a new weights hash, server version, window, ladder or
    route id each give a new id. `tools` are the entries, each with its
    spec, class, cap and taint flag. `tool_settings` is the settings file's
    section for the tools, and `wall` the executor's record of its wall, so
    a new version of the system or a moved work folder each give a new id.

    A run saves it when it starts, so a run found open under other settings
    after a restart is ended and not carried on. The saved form is hashed,
    and not the canonical one, because a tool's schema may hold a number
    with a decimal point, which the canonical form refuses.
    """
    text = saved_json(
        {
            "models": models,
            "routes": routes,
            "run": run,
            "tools": tools,
            "tool_settings": tool_settings,
            "wall": wall,
        }
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Where a config runs and not what it is: each trial of the bench has its
# own folders, and two machines have their own command PATH.
_PLACE_SETTINGS = ("work_dir", "scratch_dir", "shell_path")


def portable_config_id(
    models: Sequence[object],
    routes: Sequence[Route],
    run: RunSettings,
    tools: Sequence[ToolEntry],
    tool_settings: JsonObject,
) -> str:
    """Give one hash that names the same settings on any machine, as 64 hex characters.

    It is `config_id` without the wall and without the two tool folders and
    the command PATH. So one settings file gives one id on this Mac and in
    every trial of the bench, where each trial has a work folder of its own.
    """
    kept = {
        key: value for key, value in tool_settings.items() if key not in _PLACE_SETTINGS
    }
    text = saved_json(
        {
            "models": models,
            "routes": routes,
            "run": run,
            "tools": tools,
            "tool_settings": kept,
        }
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
